pub(crate) fn default_read_by_sender(client: &str, test_suite: bool) -> bool {
    matches!(
        client,
        "zulipandroid"
            | "zulipios"
            | "zulipdesktop"
            | "zulipmobile"
            | "zulipelectron"
            | "zulipterminal"
            | "snipe"
            | "website"
            | "ios"
            | "android"
    ) || (test_suite && client == "test suite")
}

#[cfg(test)]
mod tests {
    use super::default_read_by_sender;

    #[test]
    fn treats_interactive_clients_as_read_by_the_sender() {
        assert!(default_read_by_sender("zulipmobile", false));
        assert!(default_read_by_sender("website", false));
        assert!(!default_read_by_sender("curl", true));
        assert!(!default_read_by_sender("website ", true));
        assert!(!default_read_by_sender("test suite", false));
        assert!(default_read_by_sender("test suite", true));
    }
}
