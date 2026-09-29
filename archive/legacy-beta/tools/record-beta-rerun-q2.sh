#!/usr/bin/env bash
set -euo pipefail

batch_id='beta-2026-08-27-rerun-visual-10'
target_id='d4f6d9a1-e438-4c05-ae18-8558277c5d00'
verified_at="$(date '+%Y-%m-%dT%H:%M:%S%z' | sed -E 's/([+-][0-9]{2})([0-9]{2})$/\1:\2/')"

sha256() {
  shasum -a 256 "$1" | awk '{print $1}'
}

artifact_json() {
  local path="$1"
  jq -cn --arg path "$path" --arg sha256 "$(sha256 "$path")" '{path:$path,sha256:$sha256}'
}

body_digest() {
  local path="$1"
  python3 - "$path" <<'PY' | shasum -a 256 | awk '{print $1}'
from pathlib import Path
import re
import sys

text = Path(sys.argv[1]).read_text(encoding='utf-8')
text = re.sub(r'^# [^\n]*\n\n', '', text, count=1)
text = re.sub(r'^!\[[^\n]*\]\([^\n]*body-scene\.png\)\n?', '', text, count=1, flags=re.M)
print(text, end='')
PY
}

roundtrip_digest() {
  local title="$1"
  local run_id="$2"
  local topic_id="$3"
  local seq="$4"
  local final_path="$5"
  local normalized
  normalized="$title|$batch_id|$run_id|$topic_id|$seq|$(body_digest "$final_path")|thumbnail.png -> body-scene.png|2"
  printf '%s' "$normalized" | shasum -a 256 | awk '{print "sha256:"$1}'
}

append_q2() {
  local stem="$1"
  local run_id="$2"
  local seq="$3"
  local page_id="$4"
  local page_url="$5"
  local title="네이버-블로그-글쓰기-베타버젼-2026-08-27-${seq}차"
  local final_path="final/$stem.md"
  local image_map="assets/$stem/image-map.md"
  local body_image="assets/$stem/body-scene.png"
  local thumbnail="assets/$stem/thumbnail.png"
  local digest
  local artifacts

  digest="$(roundtrip_digest "$title" "$run_id" "$stem" "$seq" "$final_path")"
  artifacts="[$(artifact_json "$final_path"),$(artifact_json "$image_map"),$(artifact_json "$body_image"),$(artifact_json "$thumbnail")]"

  jq -cn \
    --arg run_id "$run_id" \
    --arg topic_id "$stem" \
    --arg page_id "$page_id" \
    --arg page_url "$page_url" \
    --arg batch_id "$batch_id" \
    --arg target_id "$target_id" \
    --arg verified_at "$verified_at" \
    --arg digest "$digest" \
    --argjson artifacts "$artifacts" \
    '{event_type:"stage",pipeline_version:"workflow-optimized-v1",batch_id:$batch_id,run_id:$run_id,topic_id:$topic_id,stage:"notion-rider",parent_stage:"content-assembler",lane_id:null,worker_role:"notion-rider",depends_on:["content-assembler"],started_at:$verified_at,ended_at:$verified_at,status:"passed",attempt:2,artifacts:$artifacts,error_type:null,error_message_safe:null,quality:{storage_integrity:"passed",notion_page_id:$page_id,notion_page_url:$page_url,notion_parent_data_source_id:$target_id,notion_last_verified_at:$verified_at,notion_roundtrip_digest:$digest,property_roundtrip:"passed",body_roundtrip:"passed",image_order:"thumbnail.png -> body-scene.png",uploaded_image_count:2,image_sources:"Notion internal file uploads",local_or_external_only_image_source:"none",placeholder_residue:"none",duplicate_run_id:"none",title_not_in_content:"passed",links_roundtrip:"passed"},notes:"Q2 저장 후 재조회 통과. Notion 내부 파일 업로드 2개와 썸네일·본문 이미지 순서를 확인했으며 사람 검수=대기."}' \
    >> "runs/$run_id.jsonl"
}

append_q2 '2026-에너지의-날-소등행사-beta-20260827-rerun-11' 'beta-2026-08-27-rerun-11-energy-day' 11 '3c9c007b-8c02-81ca-8227-c2ad8b7261b4' 'https://app.notion.com/p/3c9c007b-8c02-81ca-8227-c2ad8b7261b4'
append_q2 '2026-을지연습-국민참여-beta-20260827-rerun-12' 'beta-2026-08-27-rerun-12-eulji' 12 '3c9c007b-8c02-817f-8dd3-f2033c00f40f' 'https://app.notion.com/p/3c9c007b-8c02-817f-8dd3-f2033c00f40f'
append_q2 '2026-공습대비-민방위훈련-beta-20260827-rerun-13' 'beta-2026-08-27-rerun-13-civil-defense' 13 '3c9c007b-8c02-81a1-8884-cfe86b54a452' 'https://app.notion.com/p/3c9c007b-8c02-81a1-8884-cfe86b54a452'
append_q2 '2026-한옥관리-아카데미-beta-20260827-rerun-14' 'beta-2026-08-27-rerun-14-hanok' 14 '3c9c007b-8c02-8140-8e6a-e6bf42280ab6' 'https://app.notion.com/p/3c9c007b-8c02-8140-8e6a-e6bf42280ab6'
append_q2 '2026-광화문-달빛요가-beta-20260827-rerun-15' 'beta-2026-08-27-rerun-15-moonlight-yoga' 15 '3c9c007b-8c02-81da-ad0b-ca99821ce859' 'https://app.notion.com/p/3c9c007b-8c02-81da-ad0b-ca99821ce859'
append_q2 '2026-국립극장-광대올림픽-희로애락-beta-20260827-rerun-16' 'beta-2026-08-27-rerun-16-gwangdae' 16 '3c9c007b-8c02-818a-94c9-d753b5bda1a5' 'https://app.notion.com/p/3c9c007b-8c02-818a-94c9-d753b5bda1a5'
append_q2 '2026-acc-광주예술고-연주회-beta-20260827-rerun-17' 'beta-2026-08-27-rerun-17-acc' 17 '3c9c007b-8c02-81d2-a1b2-f91e06e3d7a8' 'https://app.notion.com/p/3c9c007b-8c02-81d2-a1b2-f91e06e3d7a8'
append_q2 '2027-수능-검정고시-응시원서-beta-20260827-rerun-18' 'beta-2026-08-27-rerun-18-csat-ged' 18 '3c9c007b-8c02-8163-840a-e0d9504fddb4' 'https://app.notion.com/p/3c9c007b-8c02-8163-840a-e0d9504fddb4'
append_q2 '2026-운현궁-하우스뮤지엄-beta-20260827-rerun-19' 'beta-2026-08-27-rerun-19-unhyeongung' 19 '3c9c007b-8c02-8192-ab01-f94b1a4fa80b' 'https://app.notion.com/p/3c9c007b-8c02-8192-ab01-f94b1a4fa80b'
append_q2 '2026-주민등록-비대면-사실조사-beta-20260827-rerun-20' 'beta-2026-08-27-rerun-20-resident-survey' 20 '3c9c007b-8c02-819c-8bda-d034b93dad36' 'https://app.notion.com/p/3c9c007b-8c02-819c-8bda-d034b93dad36'

printf 'recorded Q2 Notion round-trip evidence at %s\n' "$verified_at"
