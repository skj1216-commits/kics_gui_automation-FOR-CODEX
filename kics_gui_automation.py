"""
KICS authority request GUI automation starter with manual run and watch mode.

This program is intentionally selector-driven. Copy config.sample.json to
config.json and replace URLs/selectors after inspecting the closed-network pages.
"""

# 필요한 모듈들을 임포트합니다.
from __future__ import annotations

import json
import queue
import re
import threading
import traceback
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urljoin

import tkinter as tk
from tkinter import messagebox
from tkinter import ttk
from tkinter.scrolledtext import ScrolledText

# Playwright 모듈을 임포트하고, 설치되지 않은 경우 에러를 처리합니다.
try:
    from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
    from playwright.sync_api import sync_playwright
except ModuleNotFoundError as exc:
    PlaywrightTimeoutError = TimeoutError
    sync_playwright = None
    PLAYWRIGHT_IMPORT_ERROR: ModuleNotFoundError | None = exc
else:
    PLAYWRIGHT_IMPORT_ERROR = None


# 애플리케이션 디렉토리 경로를 설정합니다.
APP_DIR = Path(__file__).resolve().parent
CONFIG_PATH = APP_DIR / "config.json"
SAMPLE_CONFIG_PATH = APP_DIR / "config.sample.json"

# 처리 단계 정의: 각 단계의 키와 이름을 튜플로 정의합니다.
STEP_DEFINITIONS: list[tuple[str, str]] = [
    ("document", "공문 접속"),
    ("extract", "데이터 추출"),
    ("admin", "관리자 이동"),
    ("search", "사용자 검색"),
    ("permission", "권한 선택"),
    ("final", "최종 승인/종료"),
]

# 각 단계의 상태에 따른 스타일 (아이콘, 텍스트, 색상)을 정의합니다.
STEP_STYLES: dict[str, tuple[str, str, str]] = {
    "pending": ("○", "대기", "#6b7280"),
    "running": ("▶", "진행중", "#2563eb"),
    "done": ("✔", "완료", "#15803d"),
    "skipped": ("↷", "건너뜀", "#b45309"),
    "failed": ("❌", "실패", "#b91c1c"),
}


# 요청 데이터를 저장하는 데이터 클래스입니다.
@dataclass(frozen=True)
class RequestData:
    user_id: str  # 사용자 ID
    user_name: str  # 사용자 이름
    request_text: str  # 요청 텍스트
    permissions: list[str]  # 권한 목록


# 문서 후보를 저장하는 데이터 클래스입니다.
@dataclass(frozen=True)
class DocumentCandidate:
    key: str  # 문서 키
    title: str  # 문서 제목
    url: str  # 문서 URL
    row_text: str  # 행 텍스트


# Playwright가 설치되어 있는지 확인하고, 없으면 에러를 발생시킵니다.
def require_playwright() -> None:
    if sync_playwright is None:
        raise RuntimeError(
            "Playwright가 설치되어 있지 않습니다. "
            "명령 프롬프트에서 'pip install playwright' 후 "
            "'python -m playwright install chromium'을 실행하세요."
        ) from PLAYWRIGHT_IMPORT_ERROR


# 설정 파일을 로드합니다. config.json이 없으면 config.sample.json을 사용합니다.
def load_config() -> dict[str, Any]:
    path = CONFIG_PATH if CONFIG_PATH.exists() else SAMPLE_CONFIG_PATH
    if not path.exists():
        raise FileNotFoundError("config.json or config.sample.json is required.")
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


# 주어진 셀렉터로 요소의 텍스트를 추출합니다. 셀렉터가 없으면 빈 문자열을 반환합니다.
def selector_text(scope: Any, selector: str | None, timeout_ms: int) -> str:
    if not selector:
        return ""
    try:
        return scope.locator(selector).first.inner_text(timeout=timeout_ms).strip()
    except PlaywrightTimeoutError:
        return ""


# 주어진 패턴 목록에서 첫 번째로 매칭되는 정규식을 찾아 그룹 1을 반환합니다.
def first_regex(patterns: list[str], text: str) -> str:
    for pattern in patterns:
        match = re.search(pattern, text, re.MULTILINE | re.IGNORECASE)
        if match:
            return match.group(1).strip()
    return ""


# 문서 텍스트를 정규화하여 검색하기 쉽게 만듭니다. HTML 테이블의 inner_text를 정리합니다.
def normalize_document_text(text: str) -> str:
    """Make HTML/table inner_text easier to search without losing Korean labels."""
    lines = [" ".join(line.split()) for line in text.replace("\xa0", " ").splitlines()]
    return "\n".join(line for line in lines if line)


# 텍스트에서 공백과 구분자를 제거하여 퍼지 키워드 매칭을 위한 텍스트를 만듭니다.
def compact_text(text: str) -> str:
    """Remove whitespace and common separators for fuzzy keyword matching."""
    return re.sub(r"[\s·ㆍ\-_:/|()\[\]{}]+", "", text)


# 레이블 목록과 값 패턴을 사용하여 정규식 패턴을 생성합니다.
def label_patterns(labels: list[str], value_pattern: str) -> list[str]:
    escaped_labels = [re.escape(label) for label in labels if label]
    if not escaped_labels:
        return []
    label_group = "|".join(escaped_labels)
    return [
        rf"(?:{label_group})\s*[:：=\-\|]?\s*({value_pattern})",
        rf"(?:{label_group})\s*\n\s*({value_pattern})",
    ]


# 텍스트에서 레이블을 기반으로 값을 추출합니다.
def extract_by_labels(text: str, labels: list[str], value_pattern: str) -> str:
    for pattern in label_patterns(labels, value_pattern):
        match = re.search(pattern, text, re.MULTILINE | re.IGNORECASE)
        if match:
            return match.group(1).strip()
    return ""


# 요청 텍스트와 본문 텍스트에서 권한을 찾아 매핑합니다.
def find_permissions(
    request_text: str,
    body_text: str,
    config: dict[str, Any],
) -> tuple[list[str], list[str]]:
    permission_map: dict[str, Any] = config.get("permission_map", {})
    aliases: dict[str, list[str]] = config.get("permission_aliases", {})
    search_text = f"{request_text}\n{body_text}"
    compact_search_text = compact_text(search_text)

    matched_permissions: list[str] = []
    evidence: list[str] = []

    for keyword, mapped_value in permission_map.items():
        mapped_permissions = mapped_value if isinstance(mapped_value, list) else [mapped_value]
        keyword_aliases = [keyword, *aliases.get(keyword, [])]

        for alias in keyword_aliases:
            if not alias:
                continue
            if alias in search_text or compact_text(alias) in compact_search_text:
                matched_permissions.extend(str(permission) for permission in mapped_permissions)
                evidence.append(alias)
                break

    return list(dict.fromkeys(matched_permissions)), list(dict.fromkeys(evidence))


# 감시 모드 설정을 가져옵니다. 기본값과 설정 파일의 값을 병합합니다.
def get_watch_config(config: dict[str, Any]) -> dict[str, Any]:
    defaults: dict[str, Any] = {
        "list_url": "",
        "scan_interval_seconds": 30,
        "document_item_selector": "tr",
        "document_link_selector": "a",
        "document_link_attribute": "href",
        "document_title_selector": "",
        "document_key_selector": "",
        "document_key_attribute": "",
        "request_title_keywords": ["KICS 사용권한 부여 요청", "사용권한 부여 요청"],
        "processed_state_path": "processed_documents.json",
        "max_items_per_scan": 100,
        "process_one_per_scan": True,
        "auto_retry_default": True,
        "max_retry_count": 3,
        "retry_delay_seconds": 5,
        "popup_on_failure": True,
        "skip_failed_after_retries": True,
    }
    configured = config.get("watch", {})
    if isinstance(configured, dict):
        defaults.update(configured)
    return defaults


# 감시 상태 파일의 경로를 반환합니다.
def watch_state_path(config: dict[str, Any]) -> Path:
    watch = get_watch_config(config)
    configured_path = Path(str(watch.get("processed_state_path", "processed_documents.json")))
    if configured_path.is_absolute():
        return configured_path
    return APP_DIR / configured_path


# 감시 상태를 파일에서 로드합니다.
def load_watch_state(config: dict[str, Any]) -> dict[str, Any]:
    path = watch_state_path(config)
    if not path.exists():
        return {"documents": {}}

    try:
        with path.open("r", encoding="utf-8") as f:
            state = json.load(f)
    except json.JSONDecodeError:
        return {"documents": {}}

    if not isinstance(state, dict):
        return {"documents": {}}
    if not isinstance(state.get("documents"), dict):
        state["documents"] = {}
    return state


# 감시 상태를 파일에 저장합니다.
def save_watch_state(config: dict[str, Any], state: dict[str, Any]) -> None:
    path = watch_state_path(config)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


# 후보 문서의 결과를 상태에 기록합니다.
def mark_candidate_result(
    state: dict[str, Any],
    candidate: DocumentCandidate,
    status: str,
    attempts: int,
    error: str = "",
) -> None:
    documents = state.setdefault("documents", {})
    documents[candidate.key] = {
        "status": status,
        "attempts": attempts,
        "title": candidate.title,
        "url": candidate.url,
        "last_error": error,
    }


# 후보 문서를 건너뛸지 결정합니다.
def should_skip_candidate(
    state: dict[str, Any],
    candidate: DocumentCandidate,
    config: dict[str, Any],
    dry_run: bool,
) -> tuple[bool, str]:
    entry = state.get("documents", {}).get(candidate.key)
    if not isinstance(entry, dict):
        return False, ""

    status = str(entry.get("status", ""))
    if status == "approved":
        return True, "이미 승인 완료 기록 있음"
    if status == "cancelled":
        return True, "사용자 취소 기록 있음"
    if dry_run and status == "dry_run":
        return True, "Dry-run 검증 완료 기록 있음"

    watch = get_watch_config(config)
    if status == "failed" and bool(watch.get("skip_failed_after_retries", True)):
        return True, "이전 실패 기록 있음"

    return False, ""


# 행 텍스트가 키워드와 일치하는지 확인합니다.
def candidate_matches_keywords(row_text: str, keywords: list[str]) -> bool:
    if not keywords:
        return True
    compact_row = compact_text(row_text)
    return any(keyword in row_text or compact_text(keyword) in compact_row for keyword in keywords if keyword)


# 후보 문서의 키를 파생합니다.
def derive_candidate_key(
    item: Any,
    url: str,
    row_text: str,
    watch: dict[str, Any],
    timeout_ms: int,
) -> str:
    key = ""
    key_selector = str(watch.get("document_key_selector", "") or "")
    key_attribute = str(watch.get("document_key_attribute", "") or "")

    if key_selector:
        try:
            key_locator = item.locator(key_selector).first
            if key_attribute:
                key = (key_locator.get_attribute(key_attribute, timeout=timeout_ms) or "").strip()
            else:
                key = key_locator.inner_text(timeout=timeout_ms).strip()
        except PlaywrightTimeoutError:
            key = ""

    if not key:
        key = url.strip()
    if not key:
        key = compact_text(row_text)[:180]
    return key


# 페이지에서 문서 후보를 발견합니다.
def discover_document_candidates(page: Any, config: dict[str, Any], log: Callable[[str], None]) -> list[DocumentCandidate]:
    timeout_ms = int(config.get("timeout_ms", 10000))
    watch = get_watch_config(config)
    item_selector = str(watch.get("document_item_selector", "tr") or "tr")
    link_selector = str(watch.get("document_link_selector", "a") or "a")
    link_attribute = str(watch.get("document_link_attribute", "href") or "href")
    title_selector = str(watch.get("document_title_selector", "") or "")
    keywords = [str(keyword) for keyword in watch.get("request_title_keywords", [])]
    max_items = max(1, int(watch.get("max_items_per_scan", 100)))

    candidates: list[DocumentCandidate] = []
    seen_keys: set[str] = set()
    items = page.locator(item_selector)
    count = min(items.count(), max_items)

    for index in range(count):
        item = items.nth(index)
        try:
            row_text = normalize_document_text(item.inner_text(timeout=timeout_ms))
        except PlaywrightTimeoutError:
            continue

        if not row_text or not candidate_matches_keywords(row_text, keywords):
            continue

        try:
            link = item.locator(link_selector).first
            href = (link.get_attribute(link_attribute, timeout=timeout_ms) or "").strip()
        except PlaywrightTimeoutError:
            log(f"⚠ 후보 행에서 상세 링크를 찾지 못해 건너뜁니다: {row_text[:80]}")
            continue

        if not href:
            log(f"⚠ 후보 행의 상세 링크 속성이 비어 있어 건너뜁니다: {row_text[:80]}")
            continue

        detail_url = urljoin(page.url, href)

        title = ""
        if title_selector:
            title = selector_text(item, title_selector, timeout_ms)
        if not title:
            title = row_text.splitlines()[0][:120]

        key = derive_candidate_key(item, detail_url, row_text, watch, timeout_ms)
        if not key or key in seen_keys:
            continue
        seen_keys.add(key)

        candidates.append(DocumentCandidate(key=key, title=title, url=detail_url, row_text=row_text))

    return candidates


# 목록 URL에서 문서 후보를 스캔합니다.
def scan_document_candidates(list_url: str, config: dict[str, Any], log: Callable[[str], None]) -> list[DocumentCandidate]:
    require_playwright()
    timeout_ms = int(config.get("timeout_ms", 10000))
    storage_state = str(APP_DIR / config.get("storage_state", "auth.json"))
    headless = bool(config.get("headless", False))
    slow_mo = int(config.get("slow_mo_ms", 0))

    if not Path(storage_state).exists():
        raise FileNotFoundError("로그인 상태 파일이 없습니다. 먼저 '로그인 저장'을 실행하세요.")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless, slow_mo=slow_mo)
        context = browser.new_context(storage_state=storage_state)
        page = context.new_page()
        try:
            page.goto(list_url, wait_until="domcontentloaded", timeout=timeout_ms)
            return discover_document_candidates(page, config, log)
        finally:
            context.close()
            browser.close()


# 페이지에서 요청 데이터를 추출합니다.
def extract_request_data(page: Any, config: dict[str, Any], log: Callable[[str], None]) -> RequestData:
    timeout_ms = int(config.get("timeout_ms", 10000))
    selectors = config.get("document_selectors", {})
    raw_body_text = page.locator("body").inner_text(timeout=timeout_ms)
    body_text = normalize_document_text(raw_body_text)

    user_id = selector_text(page, selectors.get("user_id"), timeout_ms)
    user_name = selector_text(page, selectors.get("user_name"), timeout_ms)
    request_text = selector_text(page, selectors.get("request_text"), timeout_ms)

    extraction = config.get("flexible_extraction", {})
    fallback = config.get("fallback_regex", {})

    id_pattern = extraction.get("user_id_value_pattern", r"[A-Za-z][A-Za-z0-9._-]{3,}")
    name_pattern = extraction.get("user_name_value_pattern", r"[가-힣]{2,5}")

    user_id_source = "selector" if user_id else ""
    user_name_source = "selector" if user_name else ""

    if not user_id:
        user_id = extract_by_labels(body_text, extraction.get("user_id_labels", []), id_pattern)
        user_id_source = "label" if user_id else ""
    if not user_id:
        user_id = first_regex(fallback.get("user_id", []), body_text)
        user_id_source = "regex" if user_id else ""

    if not user_name:
        user_name = extract_by_labels(body_text, extraction.get("user_name_labels", []), name_pattern)
        user_name_source = "label" if user_name else ""
    if not user_name:
        user_name = first_regex(fallback.get("user_name", []), body_text)
        user_name_source = "regex" if user_name else ""

    if not request_text:
        request_text = extract_by_labels(
            body_text,
            extraction.get("request_text_labels", []),
            extraction.get("request_text_value_pattern", r".{2,120}"),
        )
    if not request_text:
        request_text = body_text

    permissions, evidence = find_permissions(request_text, body_text, config)

    if not user_id:
        raise ValueError("사용자 ID를 추출하지 못했습니다. selector, flexible_extraction.user_id_labels, fallback_regex.user_id를 확인하세요.")
    if not permissions:
        raise ValueError("요청 권한을 매핑하지 못했습니다. permission_map 또는 permission_aliases를 확인하세요.")

    if not user_name:
        log("⚠ 성명을 추출하지 못했습니다. ID와 권한 기준으로만 진행합니다.")

    log(
        "✔ 추출 결과 - "
        f"성명: {user_name or '(미확인)'}"
        f"{f'({user_name_source})' if user_name_source else ''}, "
        f"ID: {user_id}{f'({user_id_source})' if user_id_source else ''}, "
        f"권한: {', '.join(permissions)}"
    )
    if evidence:
        log(f"✔ 권한 매핑 근거 키워드: {', '.join(evidence)}")

    return RequestData(user_id=user_id, user_name=user_name, request_text=request_text, permissions=permissions)


# 모달 범위를 반환합니다. iframe이 있으면 frame_locator를 사용합니다.
def modal_scope(page: Any, config: dict[str, Any]) -> Any:
    selector = config.get("admin_selectors", {}).get("permission_iframe")
    if selector:
        return page.frame_locator(selector)
    return page


# 선택적으로 요소를 클릭합니다. 셀렉터가 없으면 아무것도 하지 않습니다.
def click_optional(scope: Any, selector: str | None, timeout_ms: int) -> None:
    if selector:
        scope.locator(selector).first.click(timeout=timeout_ms)


# 권한을 선택합니다.
def select_permission(scope: Any, permission_name: str, config: dict[str, Any], log: Callable[[str], None]) -> None:
    timeout_ms = int(config.get("timeout_ms", 10000))
    admin = config.get("admin_selectors", {})
    row_template = admin.get("permission_row_by_text", "tr:has-text('{permission}')")
    select_inside_row = admin.get("permission_select_button_inside_row", "")

    row_selector = row_template.replace("{permission}", permission_name)
    row = scope.locator(row_selector).first
    row.wait_for(timeout=timeout_ms)

    if select_inside_row:
        row.locator(select_inside_row).first.click(timeout=timeout_ms)
    else:
        row.click(timeout=timeout_ms)

    log(f"✔ 권한 선택 완료: {permission_name}")


# 브라우저 자동화를 실행하여 문서를 처리합니다.
def run_browser_automation(
    document_url: str,
    admin_url: str,
    dry_run: bool,
    require_final_confirmation: bool,
    ask_confirm: Callable[[str], bool],
    log: Callable[[str], None],
    progress: Callable[[str, str, int, str], None] | None = None,
    close_browser_after_run: bool | None = None,
) -> str:
    require_playwright()
    config = load_config()
    timeout_ms = int(config.get("timeout_ms", 10000))
    storage_state = str(APP_DIR / config.get("storage_state", "auth.json"))
    headless = bool(config.get("headless", False))
    slow_mo = int(config.get("slow_mo_ms", 0))

    def set_step(step_key: str, state: str, percent: int, status: str) -> None:
        if progress:
            progress(step_key, state, percent, status)

    if not Path(storage_state).exists():
        raise FileNotFoundError("로그인 상태 파일이 없습니다. 먼저 '로그인 저장'을 실행하세요.")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless, slow_mo=slow_mo)
        context = browser.new_context(storage_state=storage_state)
        page = context.new_page()

        try:
            set_step("document", "running", 10, "공문 페이지 접속 중")
            log("▶ 공문 페이지로 이동합니다.")
            page.goto(document_url, wait_until="domcontentloaded", timeout=timeout_ms)
            set_step("document", "done", 20, "공문 로드 완료")
            log("✔ 공문 로드 완료")

            set_step("extract", "running", 30, "공문 데이터 분석 중")
            log("▶ 공문 데이터 분석 중...")
            request = extract_request_data(page, config, log)
            set_step("extract", "done", 40, "데이터 추출 완료")

            admin = config.get("admin_selectors", {})
            set_step("admin", "running", 50, "관리자 페이지 이동 중")
            log("▶ 관리자 페이지로 이동합니다.")
            page.goto(admin_url, wait_until="domcontentloaded", timeout=timeout_ms)
            set_step("admin", "done", 55, "관리자 페이지 로드 완료")
            log("✔ 관리자 페이지 로드 완료")

            set_step("search", "running", 65, "사용자 검색 중")
            log(f"▶ 사용자 검색: {request.user_id}")
            page.locator(admin["user_id_input"]).fill(request.user_id, timeout=timeout_ms)
            page.locator(admin["search_button"]).click(timeout=timeout_ms)

            result_selector = admin.get("search_result_wait")
            if result_selector:
                result_selector = result_selector.replace("{user_id}", request.user_id)
                page.locator(result_selector).first.wait_for(timeout=timeout_ms)

            set_step("search", "done", 70, "사용자 조회 완료")
            log("✔ 사용자 조회 완료")

            set_step("permission", "running", 80, "권한 선택 처리 중")
            log("▶ 권한 선택 창을 엽니다.")
            page.locator(admin["open_permission_button"]).click(timeout=timeout_ms)
            scope = modal_scope(page, config)

            for index, permission in enumerate(request.permissions, start=1):
                log(f"▶ 권한 선택 중 ({index}/{len(request.permissions)}): {permission}")
                select_permission(scope, permission, config, log)

            click_optional(scope, admin.get("permission_confirm_button"), timeout_ms)
            set_step("permission", "done", 88, "권한 선택 완료")
            log("✔ 권한 선택 확인 단계 완료.")

            summary = (
                f"최종 처리 대상\n"
                f"성명: {request.user_name or '(미확인)'}\n"
                f"ID: {request.user_id}\n"
                f"권한: {', '.join(request.permissions)}"
            )
            log("✔ " + summary.replace("\n", " | "))

            set_step("final", "running", 93, "최종 승인 단계")
            if dry_run:
                set_step("final", "skipped", 100, "Dry-run 완료")
                log("⚠ Dry-run 모드입니다. 최종 승인/저장은 클릭하지 않습니다.")
                return "dry_run"

            if require_final_confirmation and not ask_confirm(summary + "\n\n최종 승인/저장을 클릭할까요?"):
                set_step("final", "skipped", 100, "사용자 취소")
                log("⚠ 사용자가 최종 승인을 취소했습니다.")
                return "cancelled"

            final_selector = admin.get("final_approve_button")
            if not final_selector:
                raise ValueError("final_approve_button selector가 설정되지 않았습니다.")

            page.locator(final_selector).click(timeout=timeout_ms)
            log("✔ 최종 승인/저장 클릭 완료.")

            alert_text = admin.get("expected_success_text")
            if alert_text:
                page.get_by_text(alert_text).first.wait_for(timeout=timeout_ms)
                log(f"✔ 성공 문구 확인: {alert_text}")

            set_step("final", "done", 100, "작업 완료")
            return "approved"

        finally:
            should_close = (
                bool(config.get("close_browser_after_run", False))
                if close_browser_after_run is None
                else close_browser_after_run
            )
            if should_close:
                context.close()
                browser.close()
            else:
                log("▶ 브라우저를 열어 둡니다. 확인 후 직접 닫으세요.")


# 로그인 설정을 수행합니다.
def setup_login(admin_url: str, log: Callable[[str], None], ask_confirm: Callable[[str], bool]) -> None:
    require_playwright()
    config = load_config()
    storage_state = str(APP_DIR / config.get("storage_state", "auth.json"))
    timeout_ms = int(config.get("timeout_ms", 10000))
    headless = bool(config.get("headless", False))

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        context = browser.new_context()
        page = context.new_page()
        page.goto(admin_url, wait_until="domcontentloaded", timeout=timeout_ms)
        log("▶ 브라우저에서 관리자 로그인을 완료하세요.")
        if ask_confirm("관리자 로그인을 완료한 뒤 확인을 누르세요. 로그인 상태를 저장합니다."):
            context.storage_state(path=storage_state)
            log(f"✔ 로그인 상태 저장 완료: {storage_state}")
        else:
            log("⚠ 로그인 저장이 취소되었습니다.")
        context.close()
        browser.close()


# 감시 루프를 실행합니다.
def run_watch_loop(
    list_url: str,
    admin_url: str,
    dry_run: bool,
    require_final_confirmation: bool,
    auto_retry: bool,
    retry_count: int,
    scan_interval_seconds: int,
    stop_event: threading.Event,
    ask_confirm: Callable[[str], bool],
    log: Callable[[str], None],
    progress: Callable[[str, str, int, str], None] | None,
    reset_process_ui: Callable[[], None],
    show_popup: Callable[[str, str, str], None],
) -> None:
    config = load_config()
    watch = get_watch_config(config)
    state = load_watch_state(config)
    process_one_per_scan = bool(watch.get("process_one_per_scan", True))
    retry_delay_seconds = max(1, int(watch.get("retry_delay_seconds", 5)))
    popup_on_failure = bool(watch.get("popup_on_failure", True))
    scan_interval_seconds = max(5, int(scan_interval_seconds))
    retry_limit = max(1, int(retry_count if auto_retry else 1))

    log("▶ 감시 모드를 시작합니다.")
    log(f"▶ 스캔 주기: {scan_interval_seconds}초, 자동 재시도: {'사용' if auto_retry else '미사용'}, 최대 시도: {retry_limit}회")

    while not stop_event.is_set():
        try:
            log("▶ 공문 목록을 스캔합니다.")
            candidates = scan_document_candidates(list_url, config, log)
            log(f"✔ 목록 스캔 완료: 후보 {len(candidates)}건")

            pending: list[DocumentCandidate] = []
            for candidate in candidates:
                skip, reason = should_skip_candidate(state, candidate, config, dry_run)
                if skip:
                    log(f"↷ 건너뜀: {candidate.title} ({reason})")
                else:
                    pending.append(candidate)

            if not pending:
                log("▶ 신규 처리 대상이 없습니다.")
            else:
                log(f"✔ 신규 처리 대상 {len(pending)}건 발견")
                items_to_process = pending[:1] if process_one_per_scan else pending

                for candidate in items_to_process:
                    if stop_event.is_set():
                        break

                    reset_process_ui()
                    log(f"▶ 신규 공문 처리 시작: {candidate.title}")
                    log(f"▶ 상세 URL: {candidate.url}")

                    last_error = ""
                    final_status = "failed"
                    attempts_used = 0

                    for attempt in range(1, retry_limit + 1):
                        attempts_used = attempt
                        if stop_event.is_set():
                            break

                        try:
                            if attempt > 1:
                                log(f"▶ 자동 재시도 {attempt}/{retry_limit}: {candidate.title}")
                            result = run_browser_automation(
                                document_url=candidate.url,
                                admin_url=admin_url,
                                dry_run=dry_run,
                                require_final_confirmation=require_final_confirmation,
                                ask_confirm=ask_confirm,
                                log=log,
                                progress=progress,
                                close_browser_after_run=True,
                            )
                            final_status = result
                            mark_candidate_result(state, candidate, result, attempts_used)
                            save_watch_state(config, state)
                            if result == "dry_run":
                                log(f"✔ Dry-run 처리 기록 저장: {candidate.title}")
                            elif result == "cancelled":
                                log(f"⚠ 사용자 취소 기록 저장: {candidate.title}")
                            else:
                                log(f"✔ 승인 처리 기록 저장: {candidate.title}")
                            break
                        except Exception as exc:
                            last_error = str(exc)
                            log(f"❌ 처리 실패 ({attempt}/{retry_limit}): {candidate.title} - {exc}", "error")
                            log(traceback.format_exc(), "error")

                            if attempt < retry_limit:
                                log(f"▶ {retry_delay_seconds}초 후 재시도합니다.")
                                if stop_event.wait(retry_delay_seconds):
                                    break
                            else:
                                final_status = "failed"
                                mark_candidate_result(state, candidate, "failed", attempts_used, last_error)
                                save_watch_state(config, state)
                                if popup_on_failure:
                                    show_popup("warning", "감시 처리 실패", f"{candidate.title}\n\n{last_error}")

                    if final_status == "failed":
                        log(f"⚠ 실패 기록 저장: {candidate.title}")

                    if stop_event.is_set():
                        break

        except Exception as exc:
            log(f"❌ 목록 스캔 실패: {exc}", "error")
            log(traceback.format_exc(), "error")
            if popup_on_failure:
                show_popup("warning", "감시 스캔 실패", str(exc))

        if stop_event.is_set():
            break

        log(f"▶ {scan_interval_seconds}초 후 다시 스캔합니다.")
        if stop_event.wait(scan_interval_seconds):
            break

    log("⚠ 감시 모드가 중지되었습니다.")


# 자동화 애플리케이션의 메인 GUI 클래스입니다.
class AutomationApp:
    # 초기화 메서드입니다.
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("KICS 권한 요청 처리 자동화")
        self.log_queue: queue.Queue[Any] = queue.Queue()
        self.worker: threading.Thread | None = None
        self.watch_stop_event: threading.Event | None = None
        self.step_labels: dict[str, tk.Label] = {}
        self.step_states: dict[str, str] = {key: "pending" for key, _ in STEP_DEFINITIONS}

        config = load_config()
        watch = get_watch_config(config)
        self.document_url = tk.StringVar(value=config.get("document_url", ""))
        self.watch_url = tk.StringVar(value=watch.get("list_url", ""))
        self.admin_url = tk.StringVar(value=config.get("admin_url", ""))
        self.dry_run = tk.BooleanVar(value=bool(config.get("dry_run_default", True)))
        self.final_confirm = tk.BooleanVar(value=bool(config.get("require_final_confirmation_default", True)))
        self.auto_retry = tk.BooleanVar(value=bool(watch.get("auto_retry_default", True)))
        self.scan_interval = tk.StringVar(value=str(watch.get("scan_interval_seconds", 30)))
        self.retry_count = tk.StringVar(value=str(watch.get("max_retry_count", 3)))
        self.status_text = tk.StringVar(value="상태: 대기중")
        self.progress_value = tk.IntVar(value=0)
        self.progress_text = tk.StringVar(value="0%")

        self._build_ui()
        self._reset_process_ui()
        self.root.after(100, self._drain_logs)

    # GUI를 구축합니다.
    def _build_ui(self) -> None:
        pad = {"padx": 8, "pady": 4}

        tk.Label(self.root, text="공문 URL (수동 1건)").grid(row=0, column=0, sticky="w", **pad)
        tk.Entry(self.root, textvariable=self.document_url, width=90).grid(row=0, column=1, columnspan=3, sticky="ew", **pad)

        tk.Label(self.root, text="공문 목록 URL (감시)").grid(row=1, column=0, sticky="w", **pad)
        tk.Entry(self.root, textvariable=self.watch_url, width=90).grid(row=1, column=1, columnspan=3, sticky="ew", **pad)

        tk.Label(self.root, text="관리자 URL").grid(row=2, column=0, sticky="w", **pad)
        tk.Entry(self.root, textvariable=self.admin_url, width=90).grid(row=2, column=1, columnspan=3, sticky="ew", **pad)

        options_frame = ttk.LabelFrame(self.root, text="실행 옵션")
        options_frame.grid(row=3, column=0, columnspan=4, sticky="ew", **pad)
        tk.Checkbutton(options_frame, text="Dry-run: 최종 승인/저장 안 함", variable=self.dry_run).grid(row=0, column=0, sticky="w", padx=8, pady=4)
        tk.Checkbutton(options_frame, text="최종 승인 전 확인창 표시", variable=self.final_confirm).grid(row=0, column=1, sticky="w", padx=8, pady=4)
        tk.Checkbutton(options_frame, text="자동 재시도", variable=self.auto_retry).grid(row=0, column=2, sticky="w", padx=8, pady=4)
        tk.Label(options_frame, text="스캔 주기(초)").grid(row=1, column=0, sticky="e", padx=8, pady=4)
        tk.Spinbox(options_frame, from_=5, to=3600, textvariable=self.scan_interval, width=8).grid(row=1, column=1, sticky="w", padx=8, pady=4)
        tk.Label(options_frame, text="최대 시도 횟수").grid(row=1, column=2, sticky="e", padx=8, pady=4)
        tk.Spinbox(options_frame, from_=1, to=10, textvariable=self.retry_count, width=8).grid(row=1, column=3, sticky="w", padx=8, pady=4)

        button_frame = ttk.LabelFrame(self.root, text="실행")
        button_frame.grid(row=4, column=0, columnspan=4, sticky="ew", **pad)
        tk.Button(button_frame, text="로그인 저장", command=self.start_login_setup).grid(row=0, column=0, sticky="ew", padx=8, pady=4)
        tk.Button(button_frame, text="작업 시작", command=self.start_run).grid(row=0, column=1, sticky="ew", padx=8, pady=4)
        tk.Button(button_frame, text="감시 시작", command=self.start_watch).grid(row=0, column=2, sticky="ew", padx=8, pady=4)
        tk.Button(button_frame, text="감시 중지", command=self.stop_watch).grid(row=0, column=3, sticky="ew", padx=8, pady=4)
        for column in range(4):
            button_frame.columnconfigure(column, weight=1)

        status_frame = ttk.LabelFrame(self.root, text="실행 상태")
        status_frame.grid(row=5, column=0, columnspan=4, sticky="ew", **pad)
        tk.Label(status_frame, textvariable=self.status_text, anchor="w").grid(row=0, column=0, sticky="ew", padx=8, pady=4)
        ttk.Progressbar(status_frame, maximum=100, variable=self.progress_value).grid(row=1, column=0, sticky="ew", padx=8, pady=4)
        tk.Label(status_frame, textvariable=self.progress_text, width=8, anchor="e").grid(row=1, column=1, sticky="e", padx=8, pady=4)
        status_frame.columnconfigure(0, weight=1)

        steps_frame = ttk.LabelFrame(self.root, text="처리 단계")
        steps_frame.grid(row=6, column=0, columnspan=4, sticky="ew", **pad)
        for index, (step_key, step_name) in enumerate(STEP_DEFINITIONS):
            label = tk.Label(steps_frame, anchor="w", width=22)
            label.grid(row=index // 3, column=index % 3, sticky="ew", padx=8, pady=4)
            self.step_labels[step_key] = label
        for column in range(3):
            steps_frame.columnconfigure(column, weight=1)

        log_frame = ttk.LabelFrame(self.root, text="실행 로그")
        log_frame.grid(row=7, column=0, columnspan=4, sticky="nsew", **pad)
        self.log_text = ScrolledText(log_frame, width=110, height=22)
        self.log_text.grid(row=0, column=0, sticky="nsew", padx=4, pady=4)
        self.log_text.tag_configure("info", foreground="#1f2937")
        self.log_text.tag_configure("progress", foreground="#2563eb")
        self.log_text.tag_configure("success", foreground="#15803d")
        self.log_text.tag_configure("warning", foreground="#b45309")
        self.log_text.tag_configure("error", foreground="#b91c1c")
        log_frame.columnconfigure(0, weight=1)
        log_frame.rowconfigure(0, weight=1)

        self.root.columnconfigure(1, weight=1)
        self.root.rowconfigure(7, weight=1)

    # 로그 메시지의 레벨을 추론합니다.
    def _infer_log_level(self, message: str) -> str:
        if message.startswith("❌") or "실패" in message:
            return "error"
        if message.startswith("⚠") or "경고" in message or "취소" in message:
            return "warning"
        if message.startswith("✔") or "완료" in message or "성공" in message:
            return "success"
        if message.startswith("▶") or "중" in message:
            return "progress"
        return "info"

    # 로그를 추가합니다.
    def _append_log(self, message: str, level: str | None = None) -> None:
        tag = level or self._infer_log_level(message)
        self.log_text.insert(tk.END, message + "\n", tag)
        self.log_text.see(tk.END)

    # 단계 상태를 설정합니다.
    def _set_step_state(self, step_key: str, state: str) -> None:
        if step_key not in self.step_labels:
            return
        self.step_states[step_key] = state
        icon, state_text, color = STEP_STYLES.get(state, STEP_STYLES["pending"])
        step_name = dict(STEP_DEFINITIONS)[step_key]
        self.step_labels[step_key].config(text=f"{icon} {step_name}: {state_text}", fg=color)

    # 처리 UI를 리셋합니다.
    def _reset_process_ui(self) -> None:
        self.status_text.set("상태: 대기중")
        self.progress_value.set(0)
        self.progress_text.set("0%")
        for step_key, _ in STEP_DEFINITIONS:
            self._set_step_state(step_key, "pending")

    # 현재 단계를 실패로 표시합니다.
    def _mark_current_step_failed(self) -> None:
        for step_key, _ in STEP_DEFINITIONS:
            if self.step_states.get(step_key) == "running":
                self._set_step_state(step_key, "failed")
                return
        for step_key, _ in STEP_DEFINITIONS:
            if self.step_states.get(step_key) in {"pending", "skipped"}:
                self._set_step_state(step_key, "failed")
                return

    # 로그를 기록합니다.
    def log(self, message: str, level: str | None = None) -> None:
        self.log_queue.put(("log", message, level or self._infer_log_level(message)))

    # 진행 상황을 업데이트합니다.
    def progress(self, step_key: str, state: str, percent: int, status: str) -> None:
        self.log_queue.put(("progress", step_key, state, str(percent), status))

    # 처리 UI를 비동기로 리셋합니다.
    def reset_process_ui_async(self) -> None:
        self.log_queue.put(("reset_process_ui",))

    # 상태 텍스트를 설정합니다.
    def set_status(self, message: str) -> None:
        self.log_queue.put(("status", message))

    # 팝업을 표시합니다.
    def show_popup(self, kind: str, title: str, message: str) -> None:
        self.log_queue.put(("popup", kind, title, message))

    # 사용자 확인을 요청합니다.
    def ask_confirm(self, message: str) -> bool:
        result_queue: queue.Queue[bool] = queue.Queue()

        def ask() -> None:
            result_queue.put(messagebox.askyesno("확인", message))

        self.root.after(0, ask)
        return result_queue.get()

    # 로그 큐를 처리합니다.
    def _drain_logs(self) -> None:
        while True:
            try:
                event = self.log_queue.get_nowait()
            except queue.Empty:
                break

            if isinstance(event, str):
                self._append_log(event)
                continue

            event_type = event[0]
            if event_type == "log":
                _, message, level = event
                self._append_log(message, level)
            elif event_type == "progress":
                _, step_key, state, percent_text, status = event
                percent = max(0, min(100, int(percent_text)))
                self._set_step_state(step_key, state)
                self.progress_value.set(percent)
                self.progress_text.set(f"{percent}%")
                self.status_text.set(f"상태: {status}")
            elif event_type == "status":
                _, message = event
                self.status_text.set(f"상태: {message}")
            elif event_type == "reset_process_ui":
                self._reset_process_ui()
            elif event_type == "fail_current_step":
                self._mark_current_step_failed()
            elif event_type == "popup":
                _, kind, title, message = event
                if kind == "error":
                    messagebox.showerror(title, message)
                elif kind == "warning":
                    messagebox.showwarning(title, message)
                else:
                    messagebox.showinfo(title, message)

        self.root.after(100, self._drain_logs)

    # 작업자를 시작합니다.
    def _start_worker(self, target: Callable[[], None]) -> bool:
        if self.worker and self.worker.is_alive():
            messagebox.showwarning("실행 중", "이미 작업이 실행 중입니다.")
            return False
        self.worker = threading.Thread(target=target, daemon=True)
        self.worker.start()
        return True

    # 안전하게 양의 정수를 파싱합니다.
    def _safe_positive_int(self, value: str, fallback: int, minimum: int, maximum: int) -> int:
        try:
            parsed = int(value)
        except ValueError:
            return fallback
        return max(minimum, min(maximum, parsed))

    # 로그인 설정을 시작합니다.
    def start_login_setup(self) -> None:
        admin_url = self.admin_url.get().strip()
        if not admin_url:
            messagebox.showerror("입력 필요", "관리자 URL을 입력하세요.")
            return

        def work() -> None:
            try:
                self.set_status("로그인 저장 중")
                self.log("▶ 로그인 저장을 시작합니다.")
                setup_login(admin_url, self.log, self.ask_confirm)
                self.set_status("로그인 저장 완료")
                self.show_popup("info", "로그인 저장 완료", "로그인 상태 저장 절차가 완료되었습니다.")
            except Exception as exc:
                self.set_status("로그인 저장 실패")
                self.log(f"❌ 로그인 저장 실패: {exc}", "error")
                self.log(traceback.format_exc(), "error")
                self.show_popup("error", "로그인 저장 실패", str(exc))

        self._start_worker(work)

    # 작업을 시작합니다.
    def start_run(self) -> None:
        document_url = self.document_url.get().strip()
        admin_url = self.admin_url.get().strip()
        dry_run = self.dry_run.get()
        final_confirm = self.final_confirm.get()

        if not document_url or not admin_url:
            messagebox.showerror("입력 필요", "공문 URL과 관리자 URL을 모두 입력하세요.")
            return

        self._reset_process_ui()
        self.log_text.delete("1.0", tk.END)

        def work() -> None:
            try:
                self.set_status("작업 시작")
                self.log("▶ 작업을 시작합니다.")
                result = run_browser_automation(
                    document_url=document_url,
                    admin_url=admin_url,
                    dry_run=dry_run,
                    require_final_confirmation=final_confirm,
                    ask_confirm=self.ask_confirm,
                    log=self.log,
                    progress=self.progress,
                )
                self.log("✔ 작업이 종료되었습니다.")
                if result == "dry_run":
                    self.set_status("Dry-run 완료")
                    self.show_popup("info", "Dry-run 완료", "최종 승인/저장 없이 검증 실행이 완료되었습니다.")
                elif result == "cancelled":
                    self.set_status("사용자 취소")
                    self.show_popup("warning", "최종 승인 취소", "사용자가 최종 승인/저장을 취소했습니다.")
                else:
                    self.set_status("작업 완료")
                    self.show_popup("info", "작업 완료", "요청 처리가 정상적으로 완료되었습니다.")
            except Exception as exc:
                self.log_queue.put(("fail_current_step",))
                self.set_status("작업 실패")
                self.log(f"❌ 작업 실패: {exc}", "error")
                self.log(traceback.format_exc(), "error")
                self.show_popup("error", "작업 실패 경고", str(exc))

        self._start_worker(work)

    # 감시 모드를 시작합니다.
    def start_watch(self) -> None:
        list_url = self.watch_url.get().strip()
        admin_url = self.admin_url.get().strip()
        dry_run = self.dry_run.get()
        final_confirm = self.final_confirm.get()
        auto_retry = self.auto_retry.get()
        scan_interval = self._safe_positive_int(self.scan_interval.get(), fallback=30, minimum=5, maximum=3600)
        retry_count = self._safe_positive_int(self.retry_count.get(), fallback=3, minimum=1, maximum=10)

        if not list_url or not admin_url:
            messagebox.showerror("입력 필요", "공문 목록 URL과 관리자 URL을 모두 입력하세요.")
            return

        self._reset_process_ui()
        self.log_text.delete("1.0", tk.END)
        self.watch_stop_event = threading.Event()

        def work() -> None:
            try:
                self.set_status("감시 모드 실행 중")
                run_watch_loop(
                    list_url=list_url,
                    admin_url=admin_url,
                    dry_run=dry_run,
                    require_final_confirmation=final_confirm,
                    auto_retry=auto_retry,
                    retry_count=retry_count,
                    scan_interval_seconds=scan_interval,
                    stop_event=self.watch_stop_event or threading.Event(),
                    ask_confirm=self.ask_confirm,
                    log=self.log,
                    progress=self.progress,
                    reset_process_ui=self.reset_process_ui_async,
                    show_popup=self.show_popup,
                )
                self.set_status("감시 중지")
            except Exception as exc:
                self.log_queue.put(("fail_current_step",))
                self.set_status("감시 실패")
                self.log(f"❌ 감시 실패: {exc}", "error")
                self.log(traceback.format_exc(), "error")
                self.show_popup("error", "감시 실패 경고", str(exc))

        if not self._start_worker(work):
            self.watch_stop_event = None

    # 감시 모드를 중지합니다.
    def stop_watch(self) -> None:
        if not self.watch_stop_event:
            messagebox.showinfo("감시 상태", "현재 실행 중인 감시 모드가 없습니다.")
            return
        self.watch_stop_event.set()
        self.set_status("감시 중지 요청")
        self.log("⚠ 감시 중지를 요청했습니다. 현재 안전 지점에서 중지됩니다.")


# 메인 함수입니다.
def main() -> None:
    root = tk.Tk()
    AutomationApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
