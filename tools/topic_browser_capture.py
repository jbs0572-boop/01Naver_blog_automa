from __future__ import annotations

import json
import uuid
from datetime import datetime
from typing import Final
from zoneinfo import ZoneInfo

from tools.aside_browser import AsideCliConfig, AsideReplSession, resolve_aside_cli
from tools.contract_types import ContractError, JSONMap
from tools.topic_creator_tree import candidate_observations

CREATOR_URL: Final = "https://creator-advisor.naver.com/naver_blog/sola_note"


def capture_creator_advisor() -> JSONMap:
    session = AsideReplSession(AsideCliConfig(resolve_aside_cli(), timeout_seconds=45))
    try:
        result = session.run_json(
            "const targets = await listBrowserTabs();"
            + f"const target = targets.find(t => t.url === {json.dumps(CREATOR_URL)} || t.url === {json.dumps(CREATOR_URL + '/trends')});"
            + f"if (target) await attachBrowserTab(target.targetId); else await openTab({json.dumps(CREATOR_URL)});"
            + f"if (page.url() === {json.dumps(CREATOR_URL + '/trends')}) await page.goto({json.dumps(CREATOR_URL)});"
            + "const sourceUrl = page.url();"
            + f"if (!sourceUrl.startsWith({json.dumps(CREATOR_URL)})) throw new Error('Creator Advisor login required');"
            + "const observation = await snapshot(page);"
            + "await page.getByRole('link', {name:'트렌드', exact:true}).click();"
            + "const trends = await snapshot(page);"
            + "console.log('__ASIDE_RESULT__' + JSON.stringify({source_url: sourceUrl, trends_url: page.url(), tree: observation.tree + '\\n' + trends.tree}));"
        )
    except ContractError:
        raise ContractError("Aside 연결 또는 Creator Advisor 접근 실패: Aside의 로그인 상태와 연결을 확인하세요. 모델은 호출하지 않았습니다.") from None
    finally:
        session.close()
    tree = result.get("tree") if isinstance(result, dict) else None
    if not isinstance(result, dict) or not isinstance(tree, str) or not tree:
        raise ContractError("Creator Advisor 화면을 읽지 못했습니다. 모델은 호출하지 않았습니다.")
    return {
        **result,
        "capture_id": "RAW-" + uuid.uuid4().hex,
        "captured_at": datetime.now(ZoneInfo("Asia/Seoul")).isoformat(),
        "timezone": "Asia/Seoul",
        "candidates": list(candidate_observations(tree)),
    }
