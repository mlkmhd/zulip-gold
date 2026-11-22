"use strict";

const assert = require("node:assert/strict");
const readline = require("node:readline");

const [modulePath, chrome, profile, origin] = process.argv.slice(2);
const puppeteer = require(modulePath);

const quotaHeaders = [
    "x-ratelimit-limit",
    "x-ratelimit-remaining",
    "x-ratelimit-reset",
    "retry-after",
];

async function run() {
    const browser = await puppeteer.launch({
        executablePath: chrome,
        pipe: true,
        ignoreDefaultArgs: true,
        args: [
            "--headless",
            "--no-first-run",
            "--no-default-browser-check",
            "--no-proxy-server",
            `--user-data-dir=${profile}`,
            "--host-resolver-rules=MAP zulip.testserver 127.0.0.1",
            "about:blank",
        ],
    });
    try {
        const launched = browser.process()?.spawnargs;
        assert.ok(launched);
        assert.ok(
            launched.every(
                (arg) => !/^--(?:disable-|no-sandbox|ignore-certificate|allow-insecure)/.test(arg),
            ),
            "unexpected browser security override",
        );
        const page = await browser.newPage();
        await page.setRequestInterception(true);
        page.on("request", (request) => {
            if (new URL(request.url()).origin === origin) {
                void request.continue();
            } else {
                void request.abort();
            }
        });
        const input = readline.createInterface({input: process.stdin});
        let session;
        const sessionCookie = async () =>
            (await page.browserContext().cookies()).find(
                (cookie) => cookie.name === "__Host-sessionid",
            );

        for await (const line of input) {
            const command = JSON.parse(line);
            if (command.action === "login") {
                assert.equal(session, undefined);
                const login = new URL("/login/", origin);
                login.searchParams.set("next", "/json/saved_snippets");
                const pageResponse = await page.goto(login.href, {waitUntil: "domcontentloaded"});
                assert.equal(pageResponse.status(), 200);
                assert.ok((await sessionCookie()) === undefined, "session exists before login");
                await page.waitForSelector("form#login_form input[name=csrfmiddlewaretoken]");
                await page.type("form#login_form input[name=username]", command.username);
                await page.type("form#login_form input[name=password]", command.password);
                const [response] = await Promise.all([
                    page.waitForNavigation({waitUntil: "domcontentloaded"}),
                    page.click("form#login_form button[type=submit]"),
                ]);
                assert.equal(response.status(), 200);
                assert.equal(new URL(page.url()).pathname, "/json/saved_snippets");
                const cookie = await sessionCookie();
                assert.ok(cookie && cookie.secure && cookie.httpOnly && cookie.path === "/");
                assert.equal(cookie.domain, "zulip.testserver");
                session = cookie.value;
                assert.ok(session.length > 0);
                const headers = response.headers();
                const quota = Object.fromEntries(
                    quotaHeaders.map((header) => [header, headers[header] ?? null]),
                );
                console.log(JSON.stringify({loggedIn: true, status: response.status(), quota}));
            } else if (command.action === "read") {
                assert.ok(session);
                const result = await page.evaluate(
                    async (path, marker, headers) => {
                        const response = await fetch(path, {
                            credentials: "same-origin",
                            headers: {"X-Pilot-Case": marker},
                        });
                        const quota = Object.fromEntries(
                            headers.map((header) => [header, response.headers.get(header)]),
                        );
                        return {status: response.status, body: await response.json(), quota};
                    },
                    command.path,
                    command.marker,
                    quotaHeaders,
                );
                assert.ok((await sessionCookie())?.value === session, "browser session changed");
                console.log(JSON.stringify(result));
            } else {
                throw new Error("unknown browser pilot action");
            }
        }
    } finally {
        await browser.close();
    }
}

async function main() {
    try {
        await run();
    } catch (error) {
        console.error(error.message);
        process.exitCode = 1;
    }
}

void main();
