#!/usr/bin/env bash
set -euo pipefail

printf '%s\n' '중단: 베타 모드는 Gate A 승인 이벤트를 기록하지 않습니다. Q1·manifest 검증 후 notion-rider를 실행하세요.' >&2
exit 2

batch_id='beta-2026-08-27-thumbnail-first-10'
target_id='d4f6d9a1-e438-4c05-ae18-8558277c5d00'
request_path="manifests/$batch_id-approval-request.md"
requested_at="$(awk -F': ' '/^- requested_at:/{print $2}' "$request_path" | tr -d '`')"
expires_at="$(awk -F': ' '/^- expires_at:/{print $2}' "$request_path" | tr -d '`')"
decided_at="$(date '+%Y-%m-%dT%H:%M:%S')+09:00"

run_ids='beta-2026-08-27-thumbnail-first-21-energy-day
beta-2026-08-27-thumbnail-first-22-eulji
beta-2026-08-27-thumbnail-first-23-civil-defense
beta-2026-08-27-thumbnail-first-24-hanok
beta-2026-08-27-thumbnail-first-25-moonlight-yoga
beta-2026-08-27-thumbnail-first-26-gwangdae
beta-2026-08-27-thumbnail-first-27-acc
beta-2026-08-27-thumbnail-first-28-csat-ged
beta-2026-08-27-thumbnail-first-29-unhyeongung
beta-2026-08-27-thumbnail-first-30-resident-survey'

per_run='{}'
batch_lines="$(mktemp -t beta-thumbnail-first-gate-a.XXXXXX)"
trap 'rm -f "$batch_lines"' EXIT

for run_id in $run_ids; do
  log="runs/$run_id.jsonl"
  if jq -e 'select(.event_type == "approval" and .gate == "notion_write")' "$log" >/dev/null; then
    printf 'approval already exists: %s\n' "$run_id" >&2
    exit 1
  fi
  manifest="manifests/$run_id-workflow-manifest.json"
  digest="$(jq -r '.artifact_digest' "$manifest")"
  per_run="$(jq -cn --arg id "$run_id" --arg digest "$digest" --argjson current "$per_run" '$current + {($id):$digest}')"
  printf '%s|%s\n' "$run_id" "$digest" >> "$batch_lines"
done

batch_digest="$(LC_ALL=C sort "$batch_lines" | shasum -a 256 | awk '{print $1}')"

for run_id in $run_ids; do
  log="runs/$run_id.jsonl"
  topic_id="$(jq -r --arg run_id "$run_id" 'select(.run_id == $run_id and .event_type == "stage") | .topic_id' "$log" | head -1)"
  jq -cn \
    --arg run_id "$run_id" \
    --arg batch_id "$batch_id" \
    --arg topic_id "$topic_id" \
    --arg target_id "$target_id" \
    --arg batch_digest "sha256:$batch_digest" \
    --argjson per_run "$per_run" \
    --argjson run_ids "$(printf '%s\n' $run_ids | jq -R . | jq -s .)" \
    --arg requested_at "$requested_at" \
    --arg decided_at "$decided_at" \
    --arg expires_at "$expires_at" \
    '{event_type:"approval",pipeline_version:"workflow-optimized-v1",run_id:$run_id,batch_id:$batch_id,topic_id:$topic_id,gate:"notion_write",decision:"approved",scope:"batch",target_id:$target_id,artifact_digest:$batch_digest,per_run_artifact_digests:$per_run,run_ids:$run_ids,max_items:10,requested_at:$requested_at,decided_at:$decided_at,expires_at:$expires_at}' \
    >> "$log"
done

printf 'recorded Gate A approval at %s for %s\n' "$decided_at" "$batch_id"
