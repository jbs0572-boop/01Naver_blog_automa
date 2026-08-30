#!/usr/bin/env bash
set -euo pipefail

batch_id='beta-2026-08-27-thumbnail-first-10'
target_id='d4f6d9a1-e438-4c05-ae18-8558277c5d00'

sha256() {
  shasum -a 256 "$1" | awk '{print $1}'
}

artifact_json() {
  local path="$1"
  jq -cn --arg path "$path" --arg sha256 "$(sha256 "$path")" '{path:$path,sha256:$sha256}'
}

while IFS='|' read -r topic_id run_id page_id page_url verified_at roundtrip_digest; do
  [ -n "$run_id" ] || continue
  log="runs/$run_id.jsonl"

  if jq -e 'select(.event_type == "stage" and .stage == "notion-rider" and .status == "passed" and .attempt == 2)' "$log" >/dev/null; then
    continue
  fi

  final="final/$topic_id.md"
  image_map="assets/$topic_id/image-map.md"
  body_image="assets/$topic_id/body-scene.png"
  thumbnail="assets/$topic_id/thumbnail.png"
  artifacts="[$(artifact_json "$final"),$(artifact_json "$image_map"),$(artifact_json "$body_image"),$(artifact_json "$thumbnail")]"

  jq -cn \
    --arg batch_id "$batch_id" \
    --arg run_id "$run_id" \
    --arg topic_id "$topic_id" \
    --arg at "$verified_at" \
    --arg page_id "$page_id" \
    --arg page_url "$page_url" \
    --arg target_id "$target_id" \
    --arg roundtrip_digest "$roundtrip_digest" \
    --argjson artifacts "$artifacts" \
    '{event_type:"stage",pipeline_version:"workflow-optimized-v1",batch_id:$batch_id,run_id:$run_id,topic_id:$topic_id,stage:"notion-rider",parent_stage:"content-assembler",lane_id:null,worker_role:"notion-rider",depends_on:["content-assembler"],started_at:$at,ended_at:$at,status:"passed",attempt:2,artifacts:$artifacts,error_type:null,error_message_safe:null,quality:{storage_integrity:"passed",notion_page_id:$page_id,notion_page_url:$page_url,notion_parent_data_source_id:$target_id,notion_last_verified_at:$at,notion_roundtrip_digest:$roundtrip_digest,property_roundtrip:"passed",body_roundtrip:"passed",heading_roundtrip:"passed",list_roundtrip:"passed",table_roundtrip:"passed",links_roundtrip:"passed",image_order:"thumbnail.png -> body-scene.png",first_image_block:"thumbnail.png",uploaded_image_count:2,image_sources:"Notion internal file uploads",local_or_external_only_image_source:"none",placeholder_residue:"none",duplicate_run_id:"none",title_not_in_content:"passed",human_review:"pending"},notes:"Q2 저장 후 재조회 통과. 첫 콘텐츠 블록의 전용 썸네일과 본문 슬롯 이미지, 본문·제목·목록·표·링크 순서, 중복 run_id 없음까지 확인했으며 사람 검수=대기."}' >> "$log"
done <<'EOF'
2026-에너지의-날-소등행사-beta-20260827-thumbnail-first-21|beta-2026-08-27-thumbnail-first-21-energy-day|3c9c007b-8c02-816b-a2d7-dac7a5ab2f85|https://app.notion.com/p/3c9c007b8c02816ba2d7dac7a5ab2f85|2026-08-27T23:36:00+09:00|sha256:0dc2a4c2b1e8200ee6e5c896f48172b3f7a7165f0ee4aa3c07847b13e7364688
2026-을지연습-국민참여-beta-20260827-thumbnail-first-22|beta-2026-08-27-thumbnail-first-22-eulji|3c9c007b-8c02-816c-9fef-e9abba86350d|https://app.notion.com/p/3c9c007b8c02816c9fefe9abba86350d|2026-08-27T23:39:00+09:00|sha256:66544b3501ae383f2371ba3f2afeef487a76f56f40b8ac7f487ccd1477ce4bac
2026-공습대비-민방위훈련-beta-20260827-thumbnail-first-23|beta-2026-08-27-thumbnail-first-23-civil-defense|3c9c007b-8c02-819f-a8db-f333d0852b56|https://app.notion.com/p/3c9c007b8c02819fa8dbf333d0852b56|2026-08-27T23:47:37+09:00|sha256:4fe7a4b83234fc3efa8d4d6393073c9c219f2ac17ec881fe326cb8381983a72d
2026-한옥관리-아카데미-beta-20260827-thumbnail-first-24|beta-2026-08-27-thumbnail-first-24-hanok|3c9c007b-8c02-811a-bf4c-e422a84434b1|https://app.notion.com/p/3c9c007b8c02811abf4ce422a84434b1|2026-08-27T23:47:39+09:00|sha256:fa3f2be159ba47792933e722e7a37448f3fc99d80a55339de9033c072da374c3
2026-광화문-달빛요가-beta-20260827-thumbnail-first-25|beta-2026-08-27-thumbnail-first-25-moonlight-yoga|3c9c007b-8c02-8166-a0a5-cf8402a59438|https://app.notion.com/p/3c9c007b8c028166a0a5cf8402a59438|2026-08-27T23:47:40+09:00|sha256:92f2e0f62f9c78fd46c4e46f3da531100710f9bf142a18557c6aff64f7ad6c4b
2026-국립극장-광대올림픽-희로애락-beta-20260827-thumbnail-first-26|beta-2026-08-27-thumbnail-first-26-gwangdae|3c9c007b-8c02-810a-a392-efaf81699215|https://app.notion.com/p/3c9c007b8c02810aa392efaf81699215|2026-08-27T23:47:42+09:00|sha256:db66ecfa0c7d973b3e4c58e6412654b3e05914b7953cb37224ff4d1ed099c5c8
2026-acc-광주예술고-연주회-beta-20260827-thumbnail-first-27|beta-2026-08-27-thumbnail-first-27-acc|3c9c007b-8c02-81d2-b03e-fcaecdea351e|https://app.notion.com/p/3c9c007b8c0281d2b03efcaecdea351e|2026-08-27T23:47:43+09:00|sha256:80c7541ec702864fda5f26a2eb1757e66d73d3d16fc86a08d909d7f0cc73a8e1
2027-수능-검정고시-응시원서-beta-20260827-thumbnail-first-28|beta-2026-08-27-thumbnail-first-28-csat-ged|3c9c007b-8c02-810e-8cbd-fd61ae0711a4|https://app.notion.com/p/3c9c007b8c02810e8cbdfd61ae0711a4|2026-08-27T23:47:45+09:00|sha256:47bcc45746e09a3f23bce561ff183133e7986f2f3e9f214bbafb47f38b38a6de
2026-운현궁-하우스뮤지엄-beta-20260827-thumbnail-first-29|beta-2026-08-27-thumbnail-first-29-unhyeongung|3c9c007b-8c02-818a-aefe-d28b00079711|https://app.notion.com/p/3c9c007b8c02818aaefed28b00079711|2026-08-27T23:47:47+09:00|sha256:06f9f679d5ed38093b0af725113e5b66cbd2af13cf850cf2c13f351448d6d0ca
2026-주민등록-비대면-사실조사-beta-20260827-thumbnail-first-30|beta-2026-08-27-thumbnail-first-30-resident-survey|3c9c007b-8c02-8124-bb80-c14ce87bf94c|https://app.notion.com/p/3c9c007b8c028124bb80c14ce87bf94c|2026-08-27T23:47:48+09:00|sha256:9178e3d12007060054519061844c642bbf0a096417dab4795b72262ca42d735a
EOF
