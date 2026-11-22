//! Post-authentication request contract used by the direct user-group read pilot.

use std::{collections::BTreeSet, error::Error};

use serde::Serialize;
use tokio_postgres::GenericClient;

use super::form::{Form, FormError};
use crate::group_listing::{ApiUserGroup, load_user_group_listing};

const ROLE_GUEST: i16 = 600; // Django UserProfile.ROLE_GUEST
pub(super) const PATH: &str = "/api/v1/user_groups";
pub(super) const SESSION_PATH: &str = "/json/user_groups";
pub(super) const INCLUDE_DEACTIVATED: &str = "include_deactivated_groups";

#[derive(Debug)]
pub enum ReadError {
    PrincipalUnavailable,
    Guest,
    InvalidJson,
    ExpectedBoolean,
    MalformedQuery,
    Database(Box<dyn Error + Send + Sync>),
}

#[derive(Debug, Serialize)]
pub struct GroupListingData {
    user_groups: Vec<ApiUserGroup>,
    #[serde(skip_serializing_if = "BTreeSet::is_empty")]
    ignored_parameters_unsupported: BTreeSet<String>,
}

pub(super) struct GroupReadOptions {
    pub(super) enabled: bool,
    pub(super) ignored_parameters: BTreeSet<String>,
}

fn non_guest(role: i16) -> Result<(), ReadError> {
    // This gate wraps Django's typed endpoint; check it before parsing.
    if role == ROLE_GUEST {
        return Err(ReadError::Guest);
    }
    Ok(())
}

pub(super) fn read_options(
    query: Option<&str>,
    parameter: &'static str,
) -> Result<GroupReadOptions, ReadError> {
    let form = Form::from_query(query).map_err(|_| ReadError::MalformedQuery)?;
    let enabled = form
        .json_bool(parameter)
        .map_err(|err| match err {
            FormError::InvalidJson(_) => ReadError::InvalidJson,
            FormError::ExpectedBoolean(_) => ReadError::ExpectedBoolean,
            _ => ReadError::MalformedQuery,
        })?
        .unwrap_or(false);
    let ignored_parameters = form
        .ignored_parameters(&[parameter, "client"])
        .into_iter()
        .map(str::to_owned)
        .collect();
    Ok(GroupReadOptions {
        enabled,
        ignored_parameters,
    })
}

pub(super) async fn require_non_guest(
    db: &impl GenericClient,
    authenticated_user_id: i32,
    authenticated_realm_id: i32,
) -> Result<(), ReadError> {
    let row = db
        .query_opt(
            "SELECT role FROM zulip.zerver_userprofile WHERE id = $1 AND realm_id = $2 AND is_active",
            &[&authenticated_user_id, &authenticated_realm_id],
        )
        .await
        .map_err(|error| ReadError::Database(error.into()))?
        .ok_or(ReadError::PrincipalUnavailable)?;
    let role = row
        .try_get("role")
        .map_err(|error| ReadError::Database(error.into()))?;
    non_guest(role)
}

/// Load the list for a principal that already passed the caller's existing
/// authentication and realm checks. The user and realm IDs are trusted inputs,
/// not request parameters; the look-up never uses the URL to select another realm.
///
/// This function only handles the existing endpoint-local non-guest restriction,
/// query validation and read. It does not authenticate requests, charge quotas,
/// emit activity, render error translations, or expose an HTTP route.
pub async fn read_authenticated(
    db: &impl GenericClient,
    authenticated_user_id: i32,
    authenticated_realm_id: i32,
    query: Option<&str>,
) -> Result<GroupListingData, ReadError> {
    require_non_guest(db, authenticated_user_id, authenticated_realm_id).await?;
    let options = read_options(query, INCLUDE_DEACTIVATED)?;
    let user_groups = load_user_group_listing(db, authenticated_realm_id, options.enabled)
        .await
        .map_err(ReadError::Database)?;
    Ok(GroupListingData {
        user_groups,
        ignored_parameters_unsupported: options.ignored_parameters,
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    fn listing_options(role: i16, query: Option<&str>) -> Result<GroupReadOptions, ReadError> {
        non_guest(role)?;
        read_options(query, INCLUDE_DEACTIVATED)
    }

    #[test]
    fn guest_check_precedes_query_validation() {
        assert!(matches!(
            listing_options(ROLE_GUEST, Some("include_deactivated_groups=undefined")),
            Err(ReadError::Guest)
        ));
        assert!(matches!(
            listing_options(400, Some("include_deactivated_groups=undefined")),
            Err(ReadError::InvalidJson)
        ));
        assert!(matches!(
            listing_options(400, Some("include_deactivated_groups=1")),
            Err(ReadError::ExpectedBoolean)
        ));
    }

    #[test]
    fn defaults_and_last_value_match_the_query_dict() {
        assert!(!listing_options(400, None).unwrap().enabled);
        let parsed = listing_options(
            400,
            Some("z=1&client=website&a=1&include_deactivated_groups=false&include_deactivated_groups=true"),
        )
        .unwrap();
        assert!(parsed.enabled);
        assert_eq!(
            parsed.ignored_parameters.into_iter().collect::<Vec<_>>(),
            ["a", "z"]
        );
    }
}
