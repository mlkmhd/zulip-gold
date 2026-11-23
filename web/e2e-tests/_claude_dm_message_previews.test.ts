import assert from "node:assert/strict";

import type {Page} from "puppeteer";

import * as common from "./lib/common.ts";

async function test_dm_sidebar(page: Page): Promise<void> {
    await common.log_in(page);
    await Promise.all([page.waitForNavigation(), page.click(".accept-web-ui-version")]);
    const individual_text = "پیش‌نمایش پیام فارسی";
    const group_text = "Group preview";
    await common.send_message(page, "private", {
        recipient: "hamlet@zulip.com",
        content: individual_text + " :melting_face:",
    });
    await common.send_message(page, "private", {
        recipient: "hamlet@zulip.com, cordelia@zulip.com",
        content: group_text + " :+1:",
    });
    const hamlet_id = await common.get_user_id_from_name(page, common.fullname.hamlet);
    const cordelia_id = await common.get_user_id_from_name(page, common.fullname.cordelia);
    assert.ok(hamlet_id !== undefined && cordelia_id !== undefined);
    const rows = [
        {key: String(hamlet_id), is_group: false, sidebar_text: individual_text},
        {
            key: [hamlet_id, cordelia_id].toSorted((a, b) => a - b).join(","),
            is_group: true,
            sidebar_text: group_text,
        },
    ];
    for (const {key, is_group, sidebar_text} of rows) {
        const row = `#direct-messages-list .dm-box[data-user-ids-string="${key}"][data-is-group="${is_group}"]`;
        await page.waitForSelector(`${row} .conversation-partners-list`, {visible: true});
        const text = await page.$eval(row, (element) => element.textContent ?? "");
        assert.ok(!text.includes(sidebar_text), "the sidebar should not copy message text");
    }
    const sidebar = "#direct-messages-list";
    assert.equal(await page.$(`${sidebar} .dm-message-preview`), null);
    await page.$eval(sidebar, (element) => {
        element.scrollIntoView({block: "center"});
    });
    await common.screenshot(page, "dm-conversations-desktop");
    await page.setViewport({width: 1280, height: 800});
    await page.$eval(sidebar, (element) => {
        element.scrollIntoView({block: "center"});
    });
    await common.screenshot(page, "dm-conversations-laptop");
}

await common.run_test(test_dm_sidebar);
