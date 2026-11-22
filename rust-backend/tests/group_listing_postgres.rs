#![cfg(all(feature = "http", target_os = "linux"))]

use std::os::unix::fs::{FileTypeExt, MetadataExt, PermissionsExt};
use std::{env, fs, path::PathBuf};

use _zulip_backend::{
    group_listing::load_user_group_listing,
    http::group_detail_request::{DetailKind, DetailReadError, read_authenticated_group_detail},
    http::group_listing_request::{ReadError, read_authenticated},
};
use serde::Deserialize;
use serde_json::{Value, json};
use tokio_postgres::{Config, NoTls};

#[derive(Deserialize)]
struct ReferenceCase {
    realm: i32,
    include_deactivated: bool,
    listing: Value,
}

#[derive(Deserialize)]
struct RequestCase {
    mode: String,
    user: i32,
    realm: i32,
    query: Option<String>,
    status: u16,
    payload: Value,
}

#[derive(Deserialize)]
struct DetailCase {
    mode: String,
    user: i32,
    realm: i32,
    group: i64,
    kind: DetailKind,
    query: Option<String>,
    status: u16,
    payload: Value,
}

#[tokio::test]
#[ignore = "requires a disposable PostgreSQL fixture seeded by Django"]
async fn user_group_listing_matches_django() {
    let root = PathBuf::from(env::var_os("ZULIP_GROUP_FIXTURE_ROOT").unwrap())
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
    let expected = root.join("user-group-listing.json");
    let expected_metadata = fs::symlink_metadata(&expected).unwrap();
    assert!(expected_metadata.file_type().is_file() && expected_metadata.uid() == owner);
    let cases: Vec<ReferenceCase> =
        serde_json::from_reader(fs::File::open(expected).unwrap()).unwrap();
    assert_eq!(cases.len(), 4);
    let requests = root.join("user-group-listing-requests.json");
    let metadata = fs::symlink_metadata(&requests).unwrap();
    assert!(metadata.file_type().is_file() && metadata.uid() == owner);
    let requests: Vec<RequestCase> =
        serde_json::from_reader(fs::File::open(requests).unwrap()).unwrap();
    assert_eq!(requests.len(), 30);
    assert_eq!(
        requests.iter().filter(|case| case.mode == "basic").count(),
        15
    );
    assert_eq!(
        requests
            .iter()
            .filter(|case| case.mode == "session")
            .count(),
        15
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
        let output = load_user_group_listing(&tx, case.realm, case.include_deactivated)
            .await
            .unwrap();
        assert_eq!(
            serde_json::to_value(output).unwrap(),
            case.listing,
            "realm={}, include_deactivated={}",
            case.realm,
            case.include_deactivated,
        );
    }
    assert!(
        load_user_group_listing(&tx, 0, false)
            .await
            .unwrap()
            .is_empty()
    );
    for case in requests {
        let output = read_authenticated(&tx, case.user, case.realm, case.query.as_deref()).await;
        let (status, payload) = match output {
            Ok(data) => (200, serde_json::to_value(data).unwrap()),
            Err(error) => {
                let message = match error {
                    ReadError::Guest => "Not allowed for guest users",
                    ReadError::InvalidJson => "include_deactivated_groups is not valid JSON",
                    ReadError::ExpectedBoolean => "include_deactivated_groups is not a boolean",
                    other => panic!("unexpected request contract error: {other:?}"),
                };
                (
                    400,
                    json!({"result": "error", "msg": message, "code": "BAD_REQUEST"}),
                )
            }
        };
        assert_eq!(
            status, case.status,
            "mode={}, query={:?}",
            case.mode, case.query
        );
        assert_eq!(
            payload, case.payload,
            "mode={}, query={:?}",
            case.mode, case.query
        );
        assert!(matches!(
            read_authenticated(&tx, case.user, 0, case.query.as_deref()).await,
            Err(ReadError::PrincipalUnavailable)
        ));
    }
    let detail_file = root.join("user-group-details.json");
    let detail_metadata = fs::symlink_metadata(&detail_file).unwrap();
    assert!(detail_metadata.file_type().is_file() && detail_metadata.uid() == owner);
    let details: Vec<DetailCase> =
        serde_json::from_reader(fs::File::open(detail_file).unwrap()).unwrap();
    assert_eq!(details.len(), 62);
    assert_eq!(
        details.iter().filter(|case| case.mode == "basic").count(),
        32
    );
    assert_eq!(
        details.iter().filter(|case| case.mode == "session").count(),
        30
    );
    for case in details {
        let result = read_authenticated_group_detail(
            &tx,
            case.user,
            case.realm,
            Some(case.group),
            case.kind,
            case.query.as_deref(),
        )
        .await;
        let (status, body) = match result {
            Ok(data) => (200, serde_json::to_value(data).unwrap()),
            Err(error) => {
                let field = case.kind.direct_parameter();
                let msg = match error {
                    DetailReadError::InvalidGroup => "Invalid user group".into(),
                    DetailReadError::DeactivatedGroup => "User group is deactivated.".into(),
                    DetailReadError::Request(ReadError::Guest) => {
                        "Not allowed for guest users".into()
                    }
                    DetailReadError::Request(ReadError::InvalidJson) => {
                        format!("{field} is not valid JSON")
                    }
                    DetailReadError::Request(ReadError::ExpectedBoolean) => {
                        format!("{field} is not a boolean")
                    }
                    other => panic!("unexpected detail error: {other:?}"),
                };
                (
                    400,
                    json!({"result": "error", "msg": msg, "code": "BAD_REQUEST"}),
                )
            }
        };
        assert_eq!(
            status, case.status,
            "{:?} {} {:?}",
            case.kind, case.mode, case.query
        );
        assert_eq!(
            body, case.payload,
            "{:?} {} {:?}",
            case.kind, case.mode, case.query
        );
    }
    tx.rollback().await.unwrap();
    drop(client);
    connection.await.unwrap().unwrap();
}
