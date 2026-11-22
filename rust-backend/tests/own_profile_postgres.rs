#![cfg(all(feature = "http", target_os = "linux"))]

use std::os::unix::fs::{FileTypeExt, MetadataExt, PermissionsExt};
use std::{collections::BTreeMap, env, fs, path::PathBuf};

use _zulip_backend::http::own_profile::{Presentation, read_authenticated};
use serde::Deserialize;
use serde_json::Value;
use tokio_postgres::{Config, NoTls};

#[derive(Deserialize)]
struct ReferenceCase {
    mode: String,
    user: i32,
    realm: i32,
    avatar: Option<String>,
    is_system_bot: bool,
    timezone_aliases: BTreeMap<String, String>,
    payload: Value,
}

#[tokio::test]
#[ignore = "requires the owner-private synthetic Django self-profile fixture"]
async fn own_profile_matches_django() {
    let root = PathBuf::from(env::var_os("ZULIP_OWN_PROFILE_FIXTURE_ROOT").unwrap())
        .canonicalize()
        .unwrap();
    let temp = env::temp_dir().canonicalize().unwrap();
    assert!(root != temp && root.starts_with(temp));
    let owner = fs::metadata("/proc/self").unwrap().uid();
    let private_directory = |metadata: fs::Metadata| {
        metadata.is_dir()
            && metadata.uid() == owner
            && metadata.permissions().mode() & 0o777 == 0o700
    };
    assert!(private_directory(fs::metadata(&root).unwrap()));
    let socket = PathBuf::from(env::var_os("ISOLATED_PG_SOCKET").unwrap())
        .canonicalize()
        .unwrap();
    assert!(socket.starts_with(&root) && private_directory(fs::metadata(&socket).unwrap()));
    let port = env::var("ISOLATED_PG_PORT")
        .unwrap()
        .parse::<u16>()
        .unwrap();
    let socket_file = fs::symlink_metadata(socket.join(format!(".s.PGSQL.{port}"))).unwrap();
    assert!(socket_file.file_type().is_socket() && socket_file.uid() == owner);
    let expected = root.join("own-profile.json");
    let metadata = fs::symlink_metadata(&expected).unwrap();
    assert!(metadata.file_type().is_file() && metadata.uid() == owner);
    let cases: Vec<ReferenceCase> =
        serde_json::from_reader(fs::File::open(expected).unwrap()).unwrap();
    assert_eq!(cases.len(), 11);
    assert_eq!(cases.iter().filter(|case| case.mode == "basic").count(), 6);
    assert_eq!(
        cases.iter().filter(|case| case.mode == "session").count(),
        5
    );

    let (mut client, connection) = Config::new()
        .host_path(socket)
        .port(port)
        .user(env::var("ISOLATED_PG_ROLE").unwrap())
        .dbname("zulip_test")
        .connect(NoTls)
        .await
        .unwrap();
    let connection = tokio::spawn(connection);
    let tx = client
        .build_transaction()
        .read_only(true)
        .start()
        .await
        .unwrap();
    let local = tx
        .query_one(
            "SELECT inet_server_addr() IS NULL, current_database(), current_setting('listen_addresses'), current_setting('transaction_read_only')",
            &[],
        )
        .await
        .unwrap();
    assert!(local.get::<_, bool>(0));
    assert_eq!(local.get::<_, String>(1), "zulip_test");
    assert_eq!(local.get::<_, String>(2), "");
    assert_eq!(local.get::<_, String>(3), "on");

    for case in cases {
        let presentation = || Presentation {
            avatar_url: case.avatar.as_deref(),
            is_system_bot: case.is_system_bot,
            timezone_aliases: &case.timezone_aliases,
        };
        let result = read_authenticated(&tx, case.user, case.realm, presentation())
            .await
            .unwrap()
            .unwrap();
        assert_eq!(
            serde_json::to_value(result).unwrap(),
            case.payload,
            "{}",
            case.mode
        );
        assert!(
            read_authenticated(&tx, case.user, 0, presentation())
                .await
                .unwrap()
                .is_none()
        );
    }
    tx.rollback().await.unwrap();
    drop(client);
    connection.await.unwrap().unwrap();
}
