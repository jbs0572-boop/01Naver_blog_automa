from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_manual_panel_has_batch_live_region_and_child_list() -> None:
    markup = (ROOT / "dashboard" / "index.html").read_text(encoding="utf-8")

    assert 'id="manual-batch"' in markup
    assert 'aria-live="polite"' in markup
    assert 'id="manual-batch-list"' in markup
    assert "블로그 1건" in markup


def test_scripts_load_manual_module_before_app() -> None:
    markup = (ROOT / "dashboard" / "index.html").read_text(encoding="utf-8")

    assert markup.index('src="/manual-run.js"') < markup.index('src="/app.js"')


def test_no_bulk_confirmation_control_exists() -> None:
    markup = (ROOT / "dashboard" / "index.html").read_text(encoding="utf-8")

    assert "전체 임시저장" not in markup
    assert "batch-confirm-all" not in markup


def test_batch_styles_keep_actions_tappable_and_grid_responsive() -> None:
    styles = (ROOT / "dashboard" / "styles.css").read_text(encoding="utf-8")

    assert ".manual-batch-list" in styles
    assert "grid-template-columns:repeat(3,minmax(0,1fr))" in styles
    assert "@media (max-width:900px)" in styles
    assert "@media (max-width:600px)" in styles
    assert ".manual-child-card{inline-size:100%" not in styles
    assert ".manual-child-action{min-block-size:var(--touch-target-min)" in styles


def test_dashboard_geometry_uses_named_design_tokens() -> None:
    styles = (ROOT / "dashboard" / "styles.css").read_text(encoding="utf-8")

    for token in (
        "--radius-control",
        "--radius-panel",
        "--radius-pill",
        "--control-height-field",
        "--control-height-compact",
        "--touch-target-min",
        "--icon-mark",
        "--icon-detail",
        "--icon-dot",
        "--status-dot",
        "--stage-gap",
        "--stage-segment-min-inline-size",
        "--stage-segment-height",
        "--separator-size",
    ):
        assert f"{token}:" in styles

    assert ".button{min-block-size:var(--control-height-compact)" in styles
    assert ".manual-child-action{min-block-size:var(--touch-target-min)" in styles
    assert ".stage-strip{display:flex;gap:var(--stage-gap)" in styles
    assert ".brand-mark{display:grid;place-items:center;inline-size:var(--icon-mark)" in styles
    assert "input,select{min-block-size:var(--control-height-field)" in styles
    assert ".detail-mark{display:grid;place-items:center;inline-size:var(--icon-detail)" in styles
    assert ".chip::before{content:\"\";inline-size:var(--status-dot)" in styles
    assert ".timeline-item{display:grid;grid-template-columns:var(--icon-dot)" in styles


def test_dashboard_uses_compact_operational_layout() -> None:
    markup = (ROOT / "dashboard" / "index.html").read_text(encoding="utf-8")
    styles = (ROOT / "dashboard" / "styles.css").read_text(encoding="utf-8")

    assert 'class="console-heading"' in markup
    assert "블로그 자동화 운영" in markup
    assert "실행이 안전하게" not in markup
    assert markup.index('id="connection-status"') < markup.index("<main")
    assert "@media (max-width:600px){.topbar{display:grid" in styles
    assert ".stage-segment.warning,.timeline-dot.warning,.pulse.warning{background:var(--status-warning)}" in styles
    assert "@media (min-width:601px) and (max-width:900px)" in styles
    assert ".manual-batch-list{grid-template-columns:repeat(3" in styles
    assert ".showcase-grid{grid-template-columns:repeat(2" in styles


def test_dashboard_has_one_job_inventory_with_summary_and_selected_detail() -> None:
    markup = (ROOT / "dashboard" / "index.html").read_text(encoding="utf-8")

    assert markup.count('id="task-list"') == 1
    assert 'id="run-list"' not in markup
    assert "RUN INVENTORY" not in markup
    assert all(label in markup for label in ("Job ID · 주제", "예정 / 실제 시작", "상태 · 사유", "확인된 토큰"))
    assert all(label in markup for label in ("진행 중·대기", "확인 필요·실패", "완료·취소 이력"))
    assert 'id="detail"' in markup


def test_job_table_collapses_to_priority_mobile_fields() -> None:
    styles = (ROOT / "dashboard" / "styles.css").read_text(encoding="utf-8")

    assert ".job-table-head,.task-select{display:grid" in styles
    assert "@media(max-width:767px)" in styles
    assert ".task-topic b{white-space:normal;word-break:keep-all}" in styles
    assert ".task-select>span:nth-child(5){display:none}" in styles
    assert ".task-more{inline-size:calc(100% - var(--space-8))" in styles


def test_schedule_copy_distinguishes_new_submission_from_active_jobs() -> None:
    source = (ROOT / "dashboard" / "schedule.js").read_text(encoding="utf-8")

    assert "예약 중지" in source
    assert "예약 재개" in source
    assert "새 예약 접수에만 적용" in source
    assert "input[type=\"checkbox\"]" in source
