use std::io::{self, Write};
use std::path::Path;
use std::process::{Command, Stdio};
use std::thread;

#[derive(Debug)]
pub struct CliError {
    pub error: io::Error,
    pub spawning: bool,
}

#[derive(Debug, PartialEq, Eq)]
pub struct CliOutput {
    pub success: bool,
    pub stdout: Vec<u8>,
}

/// Execute the installed KaTeX CLI without a shell. The caller owns text
/// encoding and decoding, including decoding output from unsuccessful exits.
pub fn run_cli(script: &Path, input: &[u8], is_inline: bool) -> Result<CliOutput, CliError> {
    if !cfg!(target_os = "linux") {
        return Err(CliError {
            error: io::Error::new(io::ErrorKind::Unsupported, "KaTeX CLI requires Linux"),
            spawning: true,
        });
    }
    let mut command = Command::new("node");
    command.arg(script);
    if !is_inline {
        command.arg("--display-mode");
    }
    run_command(command, input)
}

fn run_command(mut command: Command, input: &[u8]) -> Result<CliOutput, CliError> {
    #[cfg(target_os = "linux")]
    {
        use std::os::unix::process::CommandExt;

        // SAFETY: Only async-signal-safe syscalls run after fork. CLOEXEC keeps
        // Rust's exec-error pipe usable until exec, while closing inherited
        // application descriptors as subprocess.Popen(close_fds=True) does.
        unsafe {
            command.pre_exec(|| {
                if libc::syscall(
                    libc::SYS_close_range,
                    3u32,
                    u32::MAX,
                    libc::CLOSE_RANGE_CLOEXEC,
                ) != 0
                {
                    return Err(io::Error::last_os_error());
                }
                let mut action: libc::sigaction = std::mem::zeroed();
                action.sa_sigaction = libc::SIG_DFL;
                libc::sigemptyset(&mut action.sa_mask);
                if libc::sigaction(libc::SIGXFSZ, &action, std::ptr::null_mut()) != 0 {
                    return Err(io::Error::last_os_error());
                }
                Ok(())
            });
        }
    }
    let mut child = command
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::null())
        .spawn()
        .map_err(|error| CliError {
            error,
            spawning: true,
        })?;
    let mut stdin = child.stdin.take().expect("stdin is piped");
    // KaTeX can produce output before consuming all input. Drain stdout while
    // writing stdin so neither pipe can block the other indefinitely.
    let (output, written) = thread::scope(|scope| -> Result<_, CliError> {
        let writer =
            match thread::Builder::new().spawn_scoped(scope, move || stdin.write_all(input)) {
                Ok(writer) => writer,
                Err(error) => {
                    let _ = child.kill();
                    let _ = child.wait();
                    return Err(CliError {
                        error,
                        spawning: false,
                    });
                }
            };
        let output = child.wait_with_output();
        Ok((output, writer.join().expect("stdin writer does not panic")))
    })?;
    let output = output.map_err(|error| CliError {
        error,
        spawning: false,
    })?;
    if let Err(error) = written
        && error.kind() != io::ErrorKind::BrokenPipe
    {
        return Err(CliError {
            error,
            spawning: false,
        });
    }
    Ok(CliOutput {
        success: output.status.success(),
        stdout: output.stdout,
    })
}

#[cfg(all(test, unix))]
mod tests {
    use super::*;

    fn shell(script: &str) -> Command {
        let mut command = Command::new("sh");
        command.args(["-c", script]);
        command
    }

    #[test]
    fn transports_bytes_without_decoding_or_newline_conversion() {
        let input = b"\0\xff\r\n$\\alpha$$\n";
        assert_eq!(
            run_command(shell("cat"), input).unwrap(),
            CliOutput {
                success: true,
                stdout: input.to_vec(),
            },
        );
    }

    #[test]
    fn preserves_output_from_unsuccessful_exits_and_suppresses_stderr() {
        assert_eq!(
            run_command(shell("printf '\\377'; printf hidden >&2; exit 7"), b"").unwrap(),
            CliOutput {
                success: false,
                stdout: vec![255],
            },
        );
    }

    #[test]
    fn drains_output_while_writing_large_input() {
        let input = vec![b'x'; 1024 * 1024];
        let output = run_command(
            shell("dd if=/dev/zero bs=1024 count=1024 2>/dev/null; cat"),
            &input,
        )
        .unwrap();
        assert!(output.success);
        assert_eq!(output.stdout.len(), 2 * 1024 * 1024);
        assert_eq!(&output.stdout[..1024 * 1024], vec![0; 1024 * 1024]);
        assert_eq!(&output.stdout[1024 * 1024..], input);
    }

    #[test]
    fn early_stdin_closure_preserves_both_exit_outcomes() {
        let input = vec![b'x'; 1024 * 1024];
        for status in [0, 1] {
            let output = run_command(shell(&format!("exec 0<&-; exit {status}")), &input).unwrap();
            assert_eq!(output.success, status == 0);
            assert!(output.stdout.is_empty());
        }
    }

    #[test]
    fn signal_termination_is_an_unsuccessful_exit() {
        let output = run_command(shell("kill -TERM $$"), b"").unwrap();
        assert!(!output.success);
        assert!(output.stdout.is_empty());
    }

    #[test]
    fn spawn_failure_is_distinguished_from_child_failure() {
        let error = run_command(Command::new("/nonexistent/zulip-katex-node"), b"").unwrap_err();
        assert!(error.spawning);
        assert_eq!(error.error.kind(), io::ErrorKind::NotFound);
    }
}
