#![cfg(all(feature = "http", target_os = "linux"))]

use std::os::unix::fs::{FileTypeExt, MetadataExt, PermissionsExt};
use std::{
    collections::{BTreeMap, BTreeSet},
    env, fs,
    path::PathBuf,
};

use _zulip_backend::http::{
    own_profile::{ReadError, read_authenticated},
    own_profile_presentation::{
        AvatarKind, Presentation, ResolvedAvatar, TimezoneAliases, avatar_kind, system_bot,
    },
};
use serde::Deserialize;
use serde_json::Value;
use tokio_postgres::{Config, NoTls};

#[derive(Deserialize)]
struct ReferenceCase {
    mode: String,
    user: i32,
    realm: i32,
    avatar: String,
    avatar_kind: AvatarKind,
    avatar_version: i16,
    payload: Value,
}

#[derive(Deserialize)]
struct AvatarCases {
    system_bot_emails: BTreeSet<String>,
    cases: Vec<AvatarCase>,
}

#[derive(Deserialize)]
struct AvatarCase {
    email: String,
    delivery_email: String,
    source: String,
    kind: AvatarKind,
    is_system_bot: bool,
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
    let private_input = |name: &str| {
        let path = root.join(name);
        let metadata = fs::symlink_metadata(&path).unwrap();
        assert!(metadata.file_type().is_file() && metadata.uid() == owner);
        path
    };
    let expected = private_input("own-profile.json");
    let tzdata = fs::read_to_string(private_input("profile-tzdata.zi")).unwrap();
    let timezone_aliases = TimezoneAliases::parse(&tzdata).unwrap();
    let django_aliases: BTreeMap<String, String> = serde_json::from_reader(
        fs::File::open(private_input("profile-timezone-aliases.json")).unwrap(),
    )
    .unwrap();
    assert!(!django_aliases.is_empty());
    assert_eq!(timezone_aliases.entries(), &django_aliases);
    eprintln!("{} tzdata aliases match Django.", django_aliases.len());

    let avatars: AvatarCases = serde_json::from_reader(
        fs::File::open(private_input("profile-avatar-cases.json")).unwrap(),
    )
    .unwrap();
    assert_eq!(avatars.cases.len(), 12);
    for case in &avatars.cases {
        assert_eq!(
            avatar_kind(
                &case.delivery_email,
                &case.source,
                &avatars.system_bot_emails
            ),
            case.kind
        );
        assert_eq!(
            system_bot(&case.email, &avatars.system_bot_emails),
            case.is_system_bot
        );
    }
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
        let presentation_for = |user_id, realm_id, version, kind| Presentation {
            avatar: ResolvedAvatar {
                user_id,
                realm_id,
                version,
                kind,
                url: &case.avatar,
            },
            system_bot_emails: &avatars.system_bot_emails,
            timezone_aliases: &timezone_aliases,
        };
        let different = if case.avatar_kind == AvatarKind::Stored {
            AvatarKind::Default
        } else {
            AvatarKind::Stored
        };
        let presentation =
            |kind| presentation_for(case.user, case.realm, case.avatar_version, kind);
        for mismatched in [
            presentation(different),
            presentation_for(0, case.realm, case.avatar_version, case.avatar_kind),
            presentation_for(case.user, 0, case.avatar_version, case.avatar_kind),
            presentation_for(
                case.user,
                case.realm,
                case.avatar_version.wrapping_add(1),
                case.avatar_kind,
            ),
        ] {
            assert!(matches!(
                read_authenticated(&tx, case.user, case.realm, mismatched).await,
                Err(ReadError::AvatarContextMismatch)
            ));
        }
        let result = read_authenticated(&tx, case.user, case.realm, presentation(case.avatar_kind))
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
            read_authenticated(&tx, case.user, 0, presentation(case.avatar_kind))
                .await
                .unwrap()
                .is_none()
        );
    }
    tx.rollback().await.unwrap();
    drop(client);
    connection.await.unwrap().unwrap();
}
