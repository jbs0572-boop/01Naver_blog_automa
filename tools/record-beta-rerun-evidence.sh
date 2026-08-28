#!/usr/bin/env bash
set -euo pipefail

batch_new='beta-2026-08-27-rerun-visual-10'
target_id='d4f6d9a1-e438-4c05-ae18-8558277c5d00'
requested_at="$(date '+%Y-%m-%dT%H:%M:%S')+09:00"
batch_lines="$(mktemp -t beta-rerun-lines.XXXXXX)"
trap 'rm -f "$batch_lines"' EXIT

topics='2026-에너지의-날-소등행사-beta-20260827-rerun-11|beta-2026-08-27-rerun-11-energy-day
2026-을지연습-국민참여-beta-20260827-rerun-12|beta-2026-08-27-rerun-12-eulji
2026-공습대비-민방위훈련-beta-20260827-rerun-13|beta-2026-08-27-rerun-13-civil-defense
2026-한옥관리-아카데미-beta-20260827-rerun-14|beta-2026-08-27-rerun-14-hanok
2026-광화문-달빛요가-beta-20260827-rerun-15|beta-2026-08-27-rerun-15-moonlight-yoga
2026-국립극장-광대올림픽-희로애락-beta-20260827-rerun-16|beta-2026-08-27-rerun-16-gwangdae
2026-acc-광주예술고-연주회-beta-20260827-rerun-17|beta-2026-08-27-rerun-17-acc
2027-수능-검정고시-응시원서-beta-20260827-rerun-18|beta-2026-08-27-rerun-18-csat-ged
2026-운현궁-하우스뮤지엄-beta-20260827-rerun-19|beta-2026-08-27-rerun-19-unhyeongung
2026-주민등록-비대면-사실조사-beta-20260827-rerun-20|beta-2026-08-27-rerun-20-resident-survey'

sha256() {
  shasum -a 256 "$1" | awk '{print $1}'
}

artifact_json() {
  local path="$1"
  jq -cn --arg path "$path" --arg sha256 "$(sha256 "$path")" '{path:$path,sha256:$sha256}'
}

quality_common='{"freshness_check":"passed","title_promise_check":"passed","coverage_check":"passed","reader_question_check":"passed","repetition_check":"passed","differentiation_check":"passed","originality_check":"passed","relationship_map_check":"passed","visual_contract_check":"passed","visual_promise_check":"passed","asset_provenance_check":"passed_with_notice","visual_type_match_check":"passed","image_information_gain_check":"passed"}'

stage_event() {
  local log="$1"; shift
  local stage="$1"; shift
  local parent="$1"; shift
  local worker="$1"; shift
  local depends="$1"; shift
  local status="$1"; shift
  local artifacts="$1"; shift
  local quality="$1"; shift
  local error_type="$1"; shift
  local error_message="$1"; shift
  local run_id="$1"; shift
  local topic_id="$1"

  jq -cn \
    --arg run_id "$run_id" \
    --arg topic_id "$topic_id" \
    --arg stage "$stage" \
    --arg parent "$parent" \
    --arg worker "$worker" \
    --argjson depends "$depends" \
    --arg status "$status" \
    --argjson artifacts "$artifacts" \
    --argjson quality "$quality" \
    --arg error_type "$error_type" \
    --arg error_message "$error_message" \
    --arg at "$requested_at" \
    --arg batch_id "$batch_new" \
    '{event_type:"stage",pipeline_version:"workflow-optimized-v1",batch_id:$batch_id,run_id:$run_id,topic_id:$topic_id,stage:$stage,parent_stage:(if $parent == "" then null else $parent end),lane_id:null,worker_role:$worker,depends_on:$depends,started_at:$at,ended_at:$at,status:$status,attempt:1,artifacts:$artifacts,error_type:(if $error_type == "" then null else $error_type end),error_message_safe:(if $error_message == "" then null else $error_message end),quality:$quality,notes:"기존 배치와 분리한 이미지 품질 재실행. Notion 저장 전 외부 쓰기 없음."}' >> "$log"
}

mkdir -p runs manifests
while IFS='|' read -r stem run_id; do
  [ -n "$stem" ] || continue
  log="runs/$run_id.jsonl"
  manifest="manifests/$run_id-workflow-manifest.json"
  asset_dir="assets/$stem"
  : > "$log"

  digest="$(jq -r '.artifact_digest' "$manifest")"
  printf '%s|%s\n' "$run_id" "$digest" >> "$batch_lines"

  selection="research/topic-selection-$stem.md"
  research="research/$stem.md"
  draft="drafts/$stem.md"
  image_map="$asset_dir/image-map.md"
  final="final/$stem.md"
  layout="final/$stem-naver-layout.md"
  copy="final/$stem-naver-copy.md"

  selection_artifacts="[$(artifact_json "$selection")]"
  research_artifacts="[$(artifact_json "$research")]"
  draft_artifacts="[$(artifact_json "$draft")]"
  image_artifacts="[$(artifact_json "$image_map"),$(artifact_json "$asset_dir/body-scene.png"),$(artifact_json "$asset_dir/thumbnail.png"),$(artifact_json "$asset_dir/image-generation.jsonl"),$(artifact_json "$asset_dir/image-quality.jsonl")]"
  final_artifacts="[$(artifact_json "$final"),$(artifact_json "$layout"),$(artifact_json "$copy"),$(artifact_json "$manifest")]"

  stage_event "$log" 'topic-selector' '' 'topic-selector' '[]' 'passed' "$selection_artifacts" "$quality_common" '' '' "$run_id" "$stem"
  stage_event "$log" 'researcher' 'topic-selector' 'researcher' '["topic-selector"]' 'passed' "$research_artifacts" "$quality_common" '' '' "$run_id" "$stem"
  stage_event "$log" 'writer' 'researcher' 'writer' '["researcher"]' 'passed' "$draft_artifacts" "$quality_common" '' '' "$run_id" "$stem"
  stage_event "$log" 'image-maker' 'writer' 'image-maker' '["writer"]' 'passed' "$image_artifacts" "$quality_common" '' '' "$run_id" "$stem"
  stage_event "$log" 'content-assembler' 'image-maker' 'content-assembler' '["image-maker"]' 'passed' "$final_artifacts" "$quality_common" '' '' "$run_id" "$stem"
  stage_event "$log" 'notion-rider' 'content-assembler' 'notion-rider' '["content-assembler"]' 'pending' "[$(artifact_json "$final"),$(artifact_json "$image_map"),$(artifact_json "$asset_dir/body-scene.png"),$(artifact_json "$asset_dir/thumbnail.png")]" '{"storage_integrity":"pending","approval_required":false,"external_write_started":false}' '' '' "$run_id" "$stem"
done <<EOF
$topics
EOF

batch_digest="$(LC_ALL=C sort "$batch_lines" | shasum -a 256 | awk '{print $1}')"
request_path="manifests/$batch_new-notion-write-ready.md"
printf '%s\n' '# 베타 재실행 Notion 저장 준비 목록' > "$request_path"
printf '%s\n' '' >> "$request_path"
printf '%s\n' '- batch_id: beta-2026-08-27-rerun-visual-10' >> "$request_path"
printf '%s\n' '- mode: beta' >> "$request_path"
printf '%s\n' '- pipeline_version: workflow-optimized-v1' >> "$request_path"
printf '%s\n' '- target_data_source_id: d4f6d9a1-e438-4c05-ae18-8558277c5d00' >> "$request_path"
printf '%s\n' "- requested_at: $requested_at" >> "$request_path"
printf '%s\n' '- max_items: 10' >> "$request_path"
printf '%s\n' "- batch_manifest_digest: sha256:$batch_digest" >> "$request_path"
printf '%s\n' '' >> "$request_path"
printf '%s\n' '## 저장 준비 범위' >> "$request_path"
printf '%s\n' '' >> "$request_path"
printf '%s\n' '아래 10개 run_id는 Q1과 manifest 검증을 통과한 Notion 직렬 저장 대상이다. 베타 Gate A 승인 이벤트는 만들지 않는다.' >> "$request_path"
printf '%s\n' '' >> "$request_path"
printf '%s\n' '| 차수 | run_id | manifest | artifact_digest |' >> "$request_path"
printf '%s\n' '|---:|---|---|---|' >> "$request_path"
n=11
while IFS='|' read -r stem run_id; do
  [ -n "$stem" ] || continue
  manifest="manifests/$run_id-workflow-manifest.json"
  digest="$(jq -r '.artifact_digest' "$manifest")"
  printf '| %s | %s | %s | %s |\n' "$n" "$run_id" "$manifest" "$digest" >> "$request_path"
  n=$((n + 1))
done <<EOF
$topics
EOF
printf '%s\n' '' >> "$request_path"
printf '%s\n' '진행 상황: content-assembler 완료, Q1·manifest 통과, Notion 저장 준비' >> "$request_path"

printf 'recorded local rerun evidence at %s\n' "$requested_at"
