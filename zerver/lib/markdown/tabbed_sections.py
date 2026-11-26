import re
from collections.abc import Mapping
from typing import Any, Never

import markdown
from markdown.extensions import Extension
from markdown.preprocessors import Preprocessor
from typing_extensions import override

from zerver.lib.markdown.priorities import PREPROCESSOR_PRIORITIES
from zerver.lib.native_backend import load_native_contract

try:
    _rust_tabbed_text = load_native_contract("ZULIP_RUST_TABBED_TEXT", "TABBED_TEXT_API")
except ImportError:
    _rust_tabbed_text = None

_rust_normalize_prepared_tabbed_lines = getattr(
    _rust_tabbed_text, "normalize_prepared_tabbed_lines", None
)
_rust_join_prepared_tabbed_blocks = getattr(_rust_tabbed_text, "join_prepared_tabbed_blocks", None)
_rust_parse_prepared_tab_markers = getattr(_rust_tabbed_text, "parse_prepared_tab_markers", None)
_rust_build_prepared_tab_nav = getattr(_rust_tabbed_text, "build_prepared_tab_nav", None)
_rust_build_prepared_tab_content = getattr(_rust_tabbed_text, "build_prepared_tab_content", None)

START_TABBED_SECTION_REGEX = re.compile(r"^\{start_tabs\}$")
END_TABBED_SECTION_REGEX = re.compile(r"^\{end_tabs\}$")
TAB_CONTENT_REGEX = re.compile(r"^\{tab\|([^}]+)\}$")

_TAB_MARKER_PATTERNS = (START_TABBED_SECTION_REGEX, TAB_CONTENT_REGEX, END_TABBED_SECTION_REGEX)

TABBED_SECTION_TEMPLATE = """
<div class="tabbed-section {tab_class}" markdown="1">
{nav_bar}
<div class="blocks">
{blocks}
</div>
</div>
""".strip()

NAV_BAR_TEMPLATE = """
<ul class="nav">
{tabs}
</ul>
""".strip()

NAV_LIST_ITEM_TEMPLATE = """
<li class="{class_}" data-tab-key="{data_tab_key}" tabindex="0">{label}</li>
""".strip()

DIV_TAB_CONTENT_TEMPLATE = """
<div class="tab-content {class_}" data-tab-key="{data_tab_key}" markdown="1">
{content}
</div>
""".strip()

# If adding new entries here, also check if you need to update
# tabbed-instructions.js
TAB_SECTION_LABELS = {
    "desktop-web": "Desktop/Web",
    "ios": "iOS",
    "android": "Android",
    "python": "Python",
    "js": "JavaScript",
    "curl": "curl",
    "zulip-send": "zulip-send",
    "instructions-for-all-platforms": "Instructions for all platforms",
    "for-a-bot": "For a bot",
    "for-yourself": "For yourself",
    "grafana-latest": "Grafana 8.3+",
    "grafana-older-version": "Grafana 8.2 and below",
    "send-channel-message": "Send a channel message",
    "send-dm": "Send a DM",
}


def _raise_unknown_nav_tab(tab_key: object) -> Never:
    raise ValueError(
        f"Tab '{tab_key}' is not present in TAB_SECTION_LABELS in zerver/lib/markdown/tabbed_sections.py"
    )


class TabbedSectionsGenerator(Extension):
    @override
    def extendMarkdown(self, md: markdown.Markdown) -> None:
        md.preprocessors.register(
            TabbedSectionsPreprocessor(md, self.getConfigs()),
            "tabbed_sections",
            PREPROCESSOR_PRIORITIES["tabbed_sections"],
        )


def parse_tabs(lines: list[str]) -> dict[str, Any] | None:
    if (
        _rust_parse_prepared_tab_markers is not None
        and (
            START_TABBED_SECTION_REGEX,
            TAB_CONTENT_REGEX,
            END_TABBED_SECTION_REGEX,
        )
        == _TAB_MARKER_PATTERNS
    ):
        native = _rust_parse_prepared_tab_markers(lines)
        if native is not None:
            return native
    block: dict[str, Any] = {}
    for index, line in enumerate(lines):
        start_match = START_TABBED_SECTION_REGEX.search(line)
        if start_match:
            block["start_tabs_index"] = index

        tab_content_match = TAB_CONTENT_REGEX.search(line)
        if tab_content_match:
            block.setdefault("tabs", [])
            tab = {"start": index, "tab_key": tab_content_match.group(1)}
            block["tabs"].append(tab)

        end_match = END_TABBED_SECTION_REGEX.search(line)
        if end_match:
            block["end_tabs_index"] = index
            break
    return block


def generate_content_blocks(
    tab_section: dict[str, Any], lines: list[str], tab_content_template: str
) -> str:
    if _rust_build_prepared_tab_content is not None:
        return _rust_build_prepared_tab_content(
            tab_section,
            lines,
            tab_content_template,
            TAB_SECTION_LABELS,
            _rust_normalize_prepared_tabbed_lines,
            _rust_join_prepared_tabbed_blocks,
        )

    tab_content_blocks = []
    for index, tab in enumerate(tab_section["tabs"]):
        start_index = tab["start"] + 1
        try:
            # If there are more tabs, we can use the starting index
            # of the next tab as the ending index of the previous one
            end_index = tab_section["tabs"][index + 1]["start"]
        except IndexError:
            # Otherwise, just use the end of the entire section
            end_index = tab_section["end_tabs_index"]

        selected_lines = lines[start_index:end_index]
        content = (
            _rust_normalize_prepared_tabbed_lines(selected_lines)
            if _rust_normalize_prepared_tabbed_lines is not None
            else None
        )
        if content is None:
            content = "\n".join(selected_lines).strip()
        tab_content_block = tab_content_template.format(
            class_="active" if index == 0 else "",
            data_tab_key=tab["tab_key"],
            # This attribute is not used directly in this file here,
            # we need this for the current conversion script in for
            # starlight_help where this function is being imported.
            tab_label=TAB_SECTION_LABELS[tab["tab_key"]],
            # Wrapping the content in two newlines is necessary here.
            # If we don't do this, the inner Markdown does not get
            # rendered properly.
            content=f"\n{content}\n",
        )
        tab_content_blocks.append(tab_content_block)
    if _rust_join_prepared_tabbed_blocks is not None:
        joined = _rust_join_prepared_tabbed_blocks(tab_content_blocks)
        if joined is not None:
            return joined
    return "\n".join(tab_content_blocks)


class TabbedSectionsPreprocessor(Preprocessor):
    def __init__(self, md: markdown.Markdown, config: Mapping[str, Any]) -> None:
        super().__init__(md)

    @override
    def run(self, lines: list[str]) -> list[str]:
        tab_section = parse_tabs(lines)
        while tab_section:
            if "tabs" in tab_section:
                tab_class = "has-tabs"
            else:
                tab_class = "no-tabs"
                tab_section["tabs"] = [
                    {
                        "tab_key": "instructions-for-all-platforms",
                        "start": tab_section["start_tabs_index"],
                    }
                ]
            nav_bar = self.generate_nav_bar(tab_section)
            content_blocks = generate_content_blocks(tab_section, lines, DIV_TAB_CONTENT_TEMPLATE)
            rendered_tabs = TABBED_SECTION_TEMPLATE.format(
                tab_class=tab_class, nav_bar=nav_bar, blocks=content_blocks
            )

            start = tab_section["start_tabs_index"]
            end = tab_section["end_tabs_index"] + 1
            lines = [*lines[:start], rendered_tabs, *lines[end:]]
            tab_section = parse_tabs(lines)
        return lines

    def generate_nav_bar(self, tab_section: dict[str, Any]) -> str:
        if _rust_build_prepared_tab_nav is not None:
            return _rust_build_prepared_tab_nav(
                tab_section,
                TAB_SECTION_LABELS,
                NAV_LIST_ITEM_TEMPLATE,
                NAV_BAR_TEMPLATE,
                _raise_unknown_nav_tab,
            )

        li_elements = []
        for index, tab in enumerate(tab_section["tabs"]):
            tab_key = tab.get("tab_key")
            tab_label = TAB_SECTION_LABELS.get(tab_key)
            if tab_label is None:
                _raise_unknown_nav_tab(tab_key)
            class_ = "active" if index == 0 else ""

            li = NAV_LIST_ITEM_TEMPLATE.format(class_=class_, data_tab_key=tab_key, label=tab_label)
            li_elements.append(li)

        return NAV_BAR_TEMPLATE.format(tabs="\n".join(li_elements))


def makeExtension(*args: Any, **kwargs: str) -> TabbedSectionsGenerator:
    return TabbedSectionsGenerator(**kwargs)
