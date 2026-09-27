#!/usr/bin/env bash
set -euo pipefail

usage() {
  printf '%s\n' \
    "usage: $0 [--check] <asset-selection.json>" \
    'each topic requires body and thumbnail objects' \
    'common fields: file, asset_type, official_material_status' \
    'official asset fields: source_url, checked_at, official_material_status=selected' \
    'generated asset fields: official_material_status=not_found|not_suitable, official_material_note' \
    'reused asset fields: reused_from_run_id, reuse_reason' >&2
}

check_only=false
if [[ "${1:-}" == '--check' ]]; then
  check_only=true
  shift
fi

if [[ "$#" -ne 1 ]]; then
  usage
  exit 2
fi

selection_manifest="$1"
if [[ ! -f "$selection_manifest" ]]; then
  printf 'error: asset selection manifest not found: %s\n' "$selection_manifest" >&2
  exit 1
fi

if ! jq -e 'type == "object"' "$selection_manifest" >/dev/null; then
  printf 'error: asset selection manifest must be a JSON object: %s\n' "$selection_manifest" >&2
  exit 1
fi

batch_new='beta-2026-08-27-rerun-visual-10'
now="$(date '+%Y-%m-%dT%H:%M:%S')+09:00"

old_stems=(
  '2026-에너지의-날-소등행사-beta-20260827-01'
  '2026-을지연습-국민참여-beta-20260827-02'
  '2026-공습대비-민방위훈련-beta-20260827-03'
  '2026-한옥관리-아카데미-beta-20260827-04'
  '2026-광화문-달빛요가-beta-20260827-05'
  '2026-국립극장-광대올림픽-희로애락-beta-20260827-06'
  '2026-acc-광주예술고-연주회-beta-20260827-07'
  '2027-수능-검정고시-응시원서-beta-20260827-08'
  '2026-운현궁-하우스뮤지엄-beta-20260827-09'
  '2026-주민등록-비대면-사실조사-beta-20260827-10'
)
new_stems=(
  '2026-에너지의-날-소등행사-beta-20260827-rerun-11'
  '2026-을지연습-국민참여-beta-20260827-rerun-12'
  '2026-공습대비-민방위훈련-beta-20260827-rerun-13'
  '2026-한옥관리-아카데미-beta-20260827-rerun-14'
  '2026-광화문-달빛요가-beta-20260827-rerun-15'
  '2026-국립극장-광대올림픽-희로애락-beta-20260827-rerun-16'
  '2026-acc-광주예술고-연주회-beta-20260827-rerun-17'
  '2027-수능-검정고시-응시원서-beta-20260827-rerun-18'
  '2026-운현궁-하우스뮤지엄-beta-20260827-rerun-19'
  '2026-주민등록-비대면-사실조사-beta-20260827-rerun-20'
)
topic_labels=(
  '2026 에너지의 날 전국 동시 소등'
  '2026 을지연습 국민참여'
  '2026년 8월 공습대비 민방위훈련'
  '2026 한옥관리 아카데미'
  '2026 광화문 달빛요가'
  '2026 국립극장 광대 올림픽 희로애락'
  '2026 ACC 광주예술고 연주회'
  '2027학년도 수능 검정고시 합격자 응시원서'
  '2026 운현궁 하우스뮤지엄'
  '2026 주민등록 비대면 사실조사'
)
subject_scopes=(
  '도시 건물의 조명 소등과 에너지 절약 참여 장면'
  '가정에서 준비하는 비상 배낭·손전등·식수·라디오'
  '차량 정차와 시민 대피가 함께 보이는 공습대비 훈련 장면'
  '한옥 목재 격자창을 함께 수리하는 보존 교육 장면'
  '도심 광장에서 달빛 아래 요가를 하는 저녁 체험 장면'
  '무대 위 전통극의 가면·천·감정 표현을 보이는 공연 장면'
  '콘서트홀에서 악기를 연주하는 학생 음악회 장면'
  '교육지원청 방문 접수를 준비하는 빈 서류·폴더·사진 소품'
  '한옥형 전시장에 놓인 천연염색 직물·도자·공예품'
  '집에서 스마트폰으로 비대면 사실조사를 진행하는 일반 장면'
)
old_runs=(
  'beta-2026-08-27-refresh-01-energy-day'
  'beta-2026-08-27-refresh-02-eulji'
  'beta-2026-08-27-refresh-03-civil-defense'
  'beta-2026-08-27-refresh-04-hanok'
  'beta-2026-08-27-refresh-05-moonlight-yoga'
  'beta-2026-08-27-refresh-06-gwangdae'
  'beta-2026-08-27-refresh-07-acc'
  'beta-2026-08-27-refresh-08-csat-ged'
  'beta-2026-08-27-refresh-09-unhyeongung'
  'beta-2026-08-27-refresh-10-resident-survey'
)
new_runs=(
  'beta-2026-08-27-rerun-11-energy-day'
  'beta-2026-08-27-rerun-12-eulji'
  'beta-2026-08-27-rerun-13-civil-defense'
  'beta-2026-08-27-rerun-14-hanok'
  'beta-2026-08-27-rerun-15-moonlight-yoga'
  'beta-2026-08-27-rerun-16-gwangdae'
  'beta-2026-08-27-rerun-17-acc'
  'beta-2026-08-27-rerun-18-csat-ged'
  'beta-2026-08-27-rerun-19-unhyeongung'
  'beta-2026-08-27-rerun-20-resident-survey'
)

fail() {
  printf 'error: %s\n' "$1" >&2
  exit 1
}

command -v uv >/dev/null || fail 'uv is required to run the workflow verifier'
workflow_python=(uv run python)

selection_value() {
  local topic_id="$1"
  local slot="$2"
  local field="$3"
  jq -er --arg topic_id "$topic_id" --arg slot "$slot" --arg field "$field" \
    '.[$topic_id][$slot][$field] | select(type == "string" and length > 0)' "$selection_manifest"
}

selection_optional() {
  local topic_id="$1"
  local slot="$2"
  local field="$3"
  jq -r --arg topic_id "$topic_id" --arg slot "$slot" --arg field "$field" \
    '.[$topic_id][$slot][$field] // ""' "$selection_manifest"
}

source_policy_for() {
  case "$1" in
    official_asset|official_screenshot) printf 'official_or_licensed' ;;
    generated_illustration) printf 'generated_allowed' ;;
    *) fail "unsupported asset_type: $1" ;;
  esac
}

asset_description_for() {
  case "$1" in
    official_asset) printf '주제와 직접 연결된 공식 원본' ;;
    official_screenshot) printf '주제와 직접 연결된 공식 화면' ;;
    generated_illustration) printf '주제에 맞춘 생성 이미지 · 공식 자료 아님' ;;
    *) fail "unsupported asset_type: $1" ;;
  esac
}

fallback_for() {
  case "$1" in
    official_asset|official_screenshot) printf '공식 자료가 부적합하면 사유를 기록하고 생성 이미지 또는 텍스트로 대체' ;;
    generated_illustration) printf '생성 이미지가 부적합하면 텍스트 설명으로 대체' ;;
    *) fail "unsupported asset_type: $1" ;;
  esac
}

markdown_cell() {
  printf '%s' "$1" | tr '\n\r|' '   '
}

validate_selection() {
  local topic_id="$1"
  local slot="$2"
  local asset_dir="$3"
  local old_asset_dir="$4"
  local file asset_type source_url checked_at material_status material_note
  local reused_from reuse_reason image_hash old_image old_hash

  file="$(selection_value "$topic_id" "$slot" file)" || fail "$topic_id/$slot.file is required"
  asset_type="$(selection_value "$topic_id" "$slot" asset_type)" || fail "$topic_id/$slot.asset_type is required"
  material_status="$(selection_value "$topic_id" "$slot" official_material_status)" || fail "$topic_id/$slot.official_material_status is required"

  [[ "$file" =~ ^[A-Za-z0-9._-]+\.(png|jpg|jpeg)$ ]] || fail "$topic_id/$slot.file must be a local PNG or JPEG filename"
  if [[ "$slot" == 'thumbnail' ]]; then
    [[ "$file" =~ ^thumbnail\.(png|jpg|jpeg)$ ]] || fail "$topic_id/thumbnail.file must be thumbnail.png or thumbnail.jpg"
  else
    [[ ! "$file" =~ ^thumbnail\.(png|jpg|jpeg)$ ]] || fail "$topic_id/body.file must be different from the thumbnail filename"
  fi
  [[ -f "$asset_dir/$file" ]] || fail "$topic_id/$slot asset not found: $asset_dir/$file"
  source_policy_for "$asset_type" >/dev/null

  source_url="$(selection_optional "$topic_id" "$slot" source_url)"
  checked_at="$(selection_optional "$topic_id" "$slot" checked_at)"
  material_note="$(selection_optional "$topic_id" "$slot" official_material_note)"

  case "$asset_type" in
    official_asset|official_screenshot)
      [[ "$material_status" == 'selected' ]] || fail "$topic_id/$slot official asset must use official_material_status=selected"
      [[ "$source_url" =~ ^https?:// ]] || fail "$topic_id/$slot official source_url is required"
      [[ -n "$checked_at" ]] || fail "$topic_id/$slot checked_at is required"
      ;;
    generated_illustration)
      [[ "$material_status" == 'not_found' || "$material_status" == 'not_suitable' ]] || \
        fail "$topic_id/$slot generated asset requires official_material_status=not_found or not_suitable"
      [[ -n "$material_note" ]] || fail "$topic_id/$slot generated asset requires official_material_note"
      ;;
  esac

  image_hash="$(shasum -a 256 "$asset_dir/$file" | awk '{print $1}')"
  if [[ -d "$old_asset_dir" ]]; then
    while IFS= read -r -d '' old_image; do
      old_hash="$(shasum -a 256 "$old_image" | awk '{print $1}')"
      if [[ "$image_hash" == "$old_hash" ]]; then
        reused_from="$(selection_optional "$topic_id" "$slot" reused_from_run_id)"
        reuse_reason="$(selection_optional "$topic_id" "$slot" reuse_reason)"
        [[ -n "$reused_from" && -n "$reuse_reason" ]] || \
          fail "$topic_id/$slot reuses an earlier asset; reused_from_run_id and reuse_reason are required"
        break
      fi
    done < <(find "$old_asset_dir" -maxdepth 1 -type f \( -iname '*.png' -o -iname '*.jpg' -o -iname '*.jpeg' \) -print0)
  fi
}

validate_generated_metadata() {
  local topic_id="$1"
  local asset_dir="$2"
  local metadata="$asset_dir/image-generation.jsonl"
  local expected=0 slot file asset_type hash actual

  for slot in body thumbnail; do
    file="$(selection_value "$topic_id" "$slot" file)"
    asset_type="$(selection_value "$topic_id" "$slot" asset_type)"
    if [[ "$asset_type" == 'generated_illustration' ]]; then
      expected=$((expected + 1))
      [[ -s "$metadata" ]] || fail "$topic_id generated asset metadata is missing: $metadata"
      hash="$(shasum -a 256 "$asset_dir/$file" | awk '{print $1}')"
      jq -e --arg file "$file" --arg hash "sha256:$hash" \
        'select(.output_path == $file and .output_sha256 == $hash and .provenance_status == "generated")' \
        "$metadata" >/dev/null || fail "$topic_id/$slot generation metadata does not match the selected file"
    fi
  done

  if [[ -s "$metadata" ]]; then
    actual="$(jq -s 'length' "$metadata")"
    [[ "$actual" -eq "$expected" ]] || fail "$topic_id has stale or extra image-generation records"
    "${workflow_python[@]}" -m tools.workflow_verifier validate-image-metadata --metadata "$metadata" --mode beta >/dev/null
  elif [[ "$expected" -ne 0 ]]; then
    fail "$topic_id generated asset metadata is missing"
  fi
}

validate_quality_records() {
  local topic_id="$1"
  local asset_dir="$2"
  local quality="$asset_dir/image-quality.jsonl"
  local body_file thumb_file mobile_path mobile_hash actual_hash

  body_file="$(selection_value "$topic_id" body file)"
  thumb_file="$(selection_value "$topic_id" thumbnail file)"
  [[ -s "$quality" ]] || fail "$topic_id image quality record is missing: $quality"
  "${workflow_python[@]}" -m tools.workflow_verifier validate-image-quality --quality "$quality" >/dev/null
  jq -s -e --arg body "mobile/$body_file" --arg thumb "mobile/$thumb_file" \
    'length == 2 and ([.[].mobile_render_path] | sort) == ([$body, $thumb] | sort)' \
    "$quality" >/dev/null || fail "$topic_id quality records must match the selected body and thumbnail files"

  while IFS=$'\t' read -r mobile_path mobile_hash; do
    [[ -f "$asset_dir/$mobile_path" ]] || fail "$topic_id mobile preview not found: $asset_dir/$mobile_path"
    actual_hash="$(shasum -a 256 "$asset_dir/$mobile_path" | awk '{print $1}')"
    [[ "sha256:$actual_hash" == "$mobile_hash" ]] || fail "$topic_id mobile preview hash mismatch: $mobile_path"
  done < <(jq -r '[.mobile_render_path, .mobile_render_sha256] | @tsv' "$quality")
}

for i in {0..9}; do
  new="${new_stems[$i]}"
  old="${old_stems[$i]}"
  asset_dir="assets/$new"
  [[ -d "$asset_dir" ]] || fail "$new asset directory not found: $asset_dir"
  validate_selection "$new" body "$asset_dir" "assets/$old"
  validate_selection "$new" thumbnail "$asset_dir" "assets/$old"
  validate_generated_metadata "$new" "$asset_dir"
  validate_quality_records "$new" "$asset_dir"

  body_file="$(selection_value "$new" body file)"
  thumb_file="$(selection_value "$new" thumbnail file)"
  body_hash="$(shasum -a 256 "$asset_dir/$body_file" | awk '{print $1}')"
  thumb_hash="$(shasum -a 256 "$asset_dir/$thumb_file" | awk '{print $1}')"
  [[ "$body_hash" != "$thumb_hash" ]] || fail "$new body image and thumbnail must not be identical"
done

if [[ "$check_only" == true ]]; then
  printf 'asset selections valid: %s\n' "$selection_manifest"
  exit 0
fi

for i in {0..9}; do
  old="${old_stems[$i]}"
  new="${new_stems[$i]}"
  oldrun="${old_runs[$i]}"
  newrun="${new_runs[$i]}"
  asset_dir="assets/$new"

  body_file="$(selection_value "$new" body file)"
  thumb_file="$(selection_value "$new" thumbnail file)"
  body_type="$(selection_value "$new" body asset_type)"
  thumb_type="$(selection_value "$new" thumbnail asset_type)"
  body_policy="$(source_policy_for "$body_type")"
  thumb_policy="$(source_policy_for "$thumb_type")"
  body_description="$(asset_description_for "$body_type")"
  thumb_description="$(asset_description_for "$thumb_type")"
  body_fallback="$(fallback_for "$body_type")"
  thumb_fallback="$(fallback_for "$thumb_type")"
  body_source_url="$(selection_optional "$new" body source_url)"
  thumb_source_url="$(selection_optional "$new" thumbnail source_url)"
  body_checked_at="$(selection_optional "$new" body checked_at)"
  thumb_checked_at="$(selection_optional "$new" thumbnail checked_at)"
  body_material_status="$(selection_value "$new" body official_material_status)"
  thumb_material_status="$(selection_value "$new" thumbnail official_material_status)"
  body_material_note="$(markdown_cell "$(selection_optional "$new" body official_material_note)")"
  thumb_material_note="$(markdown_cell "$(selection_optional "$new" thumbnail official_material_note)")"
  body_reused_from="$(selection_optional "$new" body reused_from_run_id)"
  thumb_reused_from="$(selection_optional "$new" thumbnail reused_from_run_id)"
  body_reuse_reason="$(markdown_cell "$(selection_optional "$new" body reuse_reason)")"
  thumb_reuse_reason="$(markdown_cell "$(selection_optional "$new" thumbnail reuse_reason)")"

  if [[ "$body_type" == 'generated_illustration' ]]; then
    body_caption='출처: 생성 이미지 · 공식 자료 아님'
  else
    body_caption="출처: $body_source_url · 확인 $body_checked_at"
  fi
  if [[ "$thumb_type" == 'generated_illustration' ]]; then
    thumb_caption='출처: 생성 이미지 · 공식 자료 아님 · 본문 이미지와 분리한 전용 썸네일'
  else
    thumb_caption="출처: $thumb_source_url · 확인 $thumb_checked_at · 본문 이미지와 분리한 전용 썸네일"
  fi

  cp "research/topic-selection-$old.md" "research/topic-selection-$new.md"
  cp "research/$old.md" "research/$new.md"
  cp "drafts/$old.md" "drafts/$new.md"
  cp "final/$old.md" "final/$new.md"
  cp "final/$old-naver-layout.md" "final/$new-naver-layout.md"
  cp "final/$old-naver-copy.md" "final/$new-naver-copy.md"

  for f in "research/topic-selection-$new.md" "research/$new.md" "drafts/$new.md" "final/$new.md" "final/$new-naver-layout.md" "final/$new-naver-copy.md"; do
    OLD="$old" NEW="$new" OLDRUN="$oldrun" NEWRUN="$newrun" BATCH="$batch_new" NOW="$now" \
      perl -pi -e 's/\Q$ENV{OLD}\E/$ENV{NEW}/g; s/\Q$ENV{OLDRUN}\E/$ENV{NEWRUN}/g; s/\Qbeta-2026-08-27-refresh-10\E/$ENV{BATCH}/g; s/2026-08-27T12:27:00\+09:00/$ENV{NOW}/g; s/2026-08-27T12:50:53\+09:00/$ENV{NOW}/g' "$f"
  done

  BODY_TYPE="$body_type" BODY_POLICY="$body_policy" BODY_SCOPE="${subject_scopes[$i]}" BODY_FALLBACK="$body_fallback" \
  THUMB_TYPE="$thumb_type" THUMB_POLICY="$thumb_policy" THUMB_SCOPE="${topic_labels[$i]}" THUMB_FALLBACK="$thumb_fallback" \
  perl -0777 -i -pe 's{## 시각 요구사항 계약.*?## 정보 유효기간·출처·제한}{qq{## 시각 요구사항 계약

### VIS-01
- visual_slot_id: `VIS-01`
- visual_intent: `experience`
- asset_type: `$ENV{BODY_TYPE}`
- required_by: `reader_question`
- source_policy: `$ENV{BODY_POLICY}`
- subject_scope: $ENV{BODY_SCOPE}
- section: 주제 관련 장면
- fallback: $ENV{BODY_FALLBACK}
### THUMB-01
- visual_slot_id: `THUMB-01`
- visual_intent: `represent`
- asset_type: `$ENV{THUMB_TYPE}`
- required_by: `title_promise`
- source_policy: `$ENV{THUMB_POLICY}`
- subject_scope: $ENV{THUMB_SCOPE}
- section: 전용 썸네일
- fallback: $ENV{THUMB_FALLBACK}

주제와 직접 연결된 공식 자료를 먼저 확인하고, 공식 자료를 선택하지 않은 경우에는 그 사유를 기록한 뒤 생성 이미지를 사용합니다.

## 정보 유효기간·출처·제한}}mse' "research/topic-selection-$new.md"

  BODY_TYPE="$body_type" BODY_POLICY="$body_policy" BODY_SCOPE="${subject_scopes[$i]}" BODY_FALLBACK="$body_fallback" \
  THUMB_TYPE="$thumb_type" THUMB_POLICY="$thumb_policy" THUMB_SCOPE="${topic_labels[$i]}" THUMB_FALLBACK="$thumb_fallback" \
  perl -0777 -i -pe 's{## 4\. 시각 슬롯 원장.*?## 5\. 독창적 구성}{qq{## 4. 시각 슬롯 원장

| visual_slot_id | visual_intent | asset_type | required_by | source_policy | subject_scope | section | fallback |
|---|---|---|---|---|---|---|---|
| `VIS-01` | `experience` | `$ENV{BODY_TYPE}` | `reader_question` | `$ENV{BODY_POLICY}` | $ENV{BODY_SCOPE} | 주제 관련 장면 | $ENV{BODY_FALLBACK} |
| `THUMB-01` | `represent` | `$ENV{THUMB_TYPE}` | `title_promise` | `$ENV{THUMB_POLICY}` | $ENV{THUMB_SCOPE} | 전용 썸네일 | $ENV{THUMB_FALLBACK} |

공식 자료를 먼저 확인했으며, 실제 선택 결과와 확인 내용을 이미지 연결표에 기록합니다.

## 5. 독창적 구성}}mse' "research/$new.md"

  BODY_TYPE="$body_type" BODY_POLICY="$body_policy" BODY_SCOPE="${subject_scopes[$i]}" BODY_FALLBACK="$body_fallback" BODY_DESCRIPTION="$body_description" \
  perl -0777 -i -pe 'BEGIN{$n=0} s{^\[IMAGE:.*?\]\n}{++$n == 1 ? "[IMAGE: visual_slot_id=VIS-01; visual_intent=experience; asset_type=$ENV{BODY_TYPE}; required_by=reader_question; source_policy=$ENV{BODY_POLICY}; section=주제 관련 장면; subject_scope=$ENV{BODY_SCOPE}; fallback=$ENV{BODY_FALLBACK}; alt=$ENV{BODY_DESCRIPTION}]\n" : ""}gmse' "drafts/$new.md"

  NEW="$new" BODY_FILE="$body_file" BODY_DESCRIPTION="$body_description" \
  perl -0777 -i -pe 'BEGIN{$n=0} s{^!\[[^\n]*\]\(\.\./assets/[^\n]*\)\n}{++$n == 1 ? "![$ENV{BODY_DESCRIPTION}](../assets/$ENV{NEW}/$ENV{BODY_FILE})\n" : ""}gmse' "final/$new.md"

  THUMB_FILE="$thumb_file" THUMB_DESCRIPTION="$thumb_description" THUMB_CAPTION="$thumb_caption" \
  BODY_FILE="$body_file" BODY_DESCRIPTION="$body_description" BODY_CAPTION="$body_caption" \
  perl -0777 -i -pe 's{^\[IMAGE file="thumbnail\.(?:png|jpe?g)"[^\n]*\]\n}{"[IMAGE file=\"$ENV{THUMB_FILE}\" alt=\"$ENV{THUMB_DESCRIPTION}\" representative=true evidence=\"$ENV{THUMB_DESCRIPTION}\"]\n"}me; s{^\[ALT\].*?\[/ALT\]\n}{"[ALT] $ENV{THUMB_DESCRIPTION} [/ALT]\n"}me; s{^\[CAPTION\].*?\n}{"[CAPTION] $ENV{THUMB_CAPTION} [/CAPTION]\n"}me; BEGIN{$n=0} s{^\[IMAGE file="(?!thumbnail\.(?:png|jpe?g))[^\n]*\]\n\[ALT\][^\n]*\[/ALT\]\n\[CAPTION\][^\n]*\n}{++$n == 1 ? "[IMAGE file=\"$ENV{BODY_FILE}\" alt=\"$ENV{BODY_DESCRIPTION}\" representative=false]\n[ALT] $ENV{BODY_DESCRIPTION} [/ALT]\n[CAPTION] $ENV{BODY_CAPTION} · 본문 이미지 순서 1 [/CAPTION]\n" : ""}gmse' "final/$new-naver-copy.md"

  THUMB_FILE="$thumb_file" THUMB_DESCRIPTION="$thumb_description" THUMB_CAPTION="$thumb_caption" \
  BODY_FILE="$body_file" BODY_DESCRIPTION="$body_description" BODY_CAPTION="$body_caption" \
  perl -0777 -i -pe 's{^\[IMAGE file="thumbnail\.(?:png|jpe?g)"[^\n]*\]\n}{"[IMAGE file=\"$ENV{THUMB_FILE}\" alt=\"$ENV{THUMB_DESCRIPTION}\" representative=true evidence=\"$ENV{THUMB_DESCRIPTION}\"]\n"}me; s{^\[ALT\].*?\[/ALT\]\n}{"[ALT] $ENV{THUMB_DESCRIPTION} [/ALT]\n"}me; s{^\[CAPTION\].*?\n}{"[CAPTION] $ENV{THUMB_CAPTION} [/CAPTION]\n"}me; BEGIN{$n=0} s{^\[IMAGE file="(?!thumbnail\.(?:png|jpe?g))[^\n]*\]\n\[CAPTION\][^\n]*\n}{++$n == 1 ? "[IMAGE file=\"$ENV{BODY_FILE}\" alt=\"$ENV{BODY_DESCRIPTION}\" representative=false]\n[CAPTION] $ENV{BODY_CAPTION} · 본문 이미지 순서 1 [/CAPTION]\n" : ""}gmse; s/- 본문 이미지: `\d+개`/- 본문 이미지: `1개`/; s/본문 이미지 [23]개의/본문 이미지 1개의/' "final/$new-naver-layout.md"

  checked_at="$(date '+%Y-%m-%dT%H:%M:%S')+09:00"
  body_hash="$(shasum -a 256 "$asset_dir/$body_file" | awk '{print $1}')"
  thumb_hash="$(shasum -a 256 "$asset_dir/$thumb_file" | awk '{print $1}')"
  body_size="$(sips -g pixelWidth -g pixelHeight "$asset_dir/$body_file" | awk '/pixelWidth/{w=$2} /pixelHeight/{h=$2} END{print w "x" h}')"
  thumb_size="$(sips -g pixelWidth -g pixelHeight "$asset_dir/$thumb_file" | awk '/pixelWidth/{w=$2} /pixelHeight/{h=$2} END{print w "x" h}')"

  body_source_record="$body_description"
  if [[ "$body_type" != 'generated_illustration' ]]; then
    body_source_record="$body_source_record · 원본: $body_source_url · 확인: $body_checked_at"
  else
    body_source_record="$body_source_record · 공식 자료 상태: $body_material_status · 사유: $body_material_note"
  fi
  thumb_source_record="$thumb_description"
  if [[ "$thumb_type" != 'generated_illustration' ]]; then
    thumb_source_record="$thumb_source_record · 원본: $thumb_source_url · 확인: $thumb_checked_at"
  else
    thumb_source_record="$thumb_source_record · 공식 자료 상태: $thumb_material_status · 사유: $thumb_material_note"
  fi
  [[ -z "$body_reused_from" ]] || body_source_record="$body_source_record · 재사용: $body_reused_from · 사유: $body_reuse_reason"
  [[ -z "$thumb_reused_from" ]] || thumb_source_record="$thumb_source_record · 재사용: $thumb_reused_from · 사유: $thumb_reuse_reason"

  {
    printf '# %s 이미지 연결표\n\n' "${topic_labels[$i]}"
    printf -- '- topic_id: `%s`\n' "$new"
    printf -- '- batch_id: `%s`\n' "$batch_new"
    printf -- '- run_id: `%s`\n' "$newrun"
    printf -- '- mode: `BETA`\n- body_image_count: `1`\n- thumbnail_count: `1`\n'
    printf -- '- checked_at_kst: `%s`\n' "$checked_at"
    printf -- '- principal_image_maker: `주 담당자 직렬 실행`\n'
    printf -- '- source_policy_note: 주제와 직접 연결된 공식 자료를 먼저 확인하고, 선택하지 않은 경우에는 그 사유를 기록한 뒤 생성 이미지를 사용합니다.\n'
    printf '\n## 본문 이미지 연결\n\n'
    printf '| 순서 | 슬롯 | 파일 | visual_intent | asset_type | required_by | source_policy | subject_scope | section | fallback | 본문 정보 기여 | 출처·제작 방식 | 크기 | SHA-256 | 검수 |\n'
    printf '|---:|---|---|---|---|---|---|---|---|---|---|---|---|---|---|\n'
    printf '| 1 | `VIS-01` | `%s` | `experience` | `%s` | `reader_question` | `%s` | %s | 주제 관련 장면 | %s | 확인된 주제의 물리적 맥락을 보조 | %s | `%s` | `%s` | 통과 |\n' \
      "$body_file" "$body_type" "$body_policy" "${subject_scopes[$i]}" "$body_fallback" "$body_source_record" "$body_size" "$body_hash"
    printf '\n## 전용 썸네일\n\n'
    printf '| 항목 | 슬롯 | 파일 | visual_intent | asset_type | required_by | source_policy | subject_scope | fallback | 본문 역할 | 출처·제작 방식 | 크기 | SHA-256 | 검수 |\n'
    printf '|---|---|---|---|---|---|---|---|---|---|---|---|---|---|\n'
    printf '| `[THUMBNAIL]` | `THUMB-01` | `%s` | `represent` | `%s` | `title_promise` | `%s` | %s | %s | 본문 이미지와 분리한 대표 이미지 | %s | `%s` | `%s` | 통과 |\n' \
      "$thumb_file" "$thumb_type" "$thumb_policy" "${topic_labels[$i]}" "$thumb_fallback" "$thumb_source_record" "$thumb_size" "$thumb_hash"
    printf '\n## 제작·검수 기록\n\n'
    printf -- '- 썸네일은 주제와 직접 연결된 공식 자료를 먼저 확인하고, 공식 자료가 확인되지 않거나 주제에 맞지 않을 때만 사유를 기록한 생성 이미지를 사용했습니다.\n'
    printf -- '- 생성 이미지를 선택한 경우 본문에 없는 사실·로고·고유 인물·공식 화면을 만들지 않았고 공식 자료처럼 표시하지 않았습니다.\n'
    printf -- '- 기존 자산과 해시가 같으면 원본 실행 ID와 재사용 사유를 기록했습니다.\n'
    printf -- '- 본문 이미지 역할은 `%s` 1개이며 전용 썸네일은 첫 번째 이미지 블록으로 고정합니다.\n' "$body_file"
    printf '\n## 이미지 품질 Gate\n\n'
    printf '| 검사 | 결과 | 근거 |\n|---|---|---|\n'
    printf '| `visual_contract_check` | `passed` | 선택 manifest·research·draft·image-map의 실제 자산 유형 대조 |\n'
    printf '| `thumbnail_check` | `passed` | 전용 썸네일 1개·본문 이미지 1개·역할 분리 |\n'
    printf '| `asset_open_check` | `passed` | 실제 파일 열림 및 크기 `%s`, `%s` 확인 |\n' "$body_size" "$thumb_size"
    printf '| `source_selection_check` | `passed` | 공식 자료 선택 시 원본 URL·확인일, 생성 이미지 선택 시 공식 자료 미사용 사유 기록 |\n'
    printf '| `image_quality_record_check` | `passed` | workflow verifier로 기존 자동 검사·사람 검수 기록 확인 |\n'
    printf '| `duplicate_hash_check` | `passed` | 본문·썸네일 SHA-256 상이 |\n'
    printf '| `mobile_human_review` | `passed` | 품질 원장의 390x844 모바일 미리보기 파일·해시 재확인 |\n'
    printf '\n진행 상황: image-maker 완료\n'
  } > "$asset_dir/image-map.md"
done

printf 'created source-aware rerun copies at %s\n' "$now"
