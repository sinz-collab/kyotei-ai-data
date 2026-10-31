#!/usr/bin/env bash
set -euo pipefail

target_date="${1:?usage: publish_morning_data.sh YYYY-MM-DD}"
if [[ ! "$target_date" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}$ ]] ||
  [ "$(date -d "$target_date" +%F 2>/dev/null)" != "$target_date" ]; then
  echo "Invalid target date: $target_date" >&2
  exit 1
fi

export TARGET_DATE="$target_date"
course_target_date="$(date -d "$TARGET_DATE -1 day" +%F)"
course_cache_dir="$RUNNER_TEMP/player-course-cache"

# Display-only enrichment is soft-fail by contract. The previous cache remains
# usable when the official archive is delayed or unavailable.
python automation/player_course_6m.py daily \
  --date "$course_target_date" --soft-fail
rm -rf "$course_cache_dir"
cp -a data/player_course_6m "$course_cache_dir"

run_publication_pipeline() {
  local before_dir="$1"
  local log_suffix="$2"

  python automation/build_site_data.py --date "$TARGET_DATE" \
    | tee "build-${log_suffix}.log"
  python automation/enrich_motor_recent.py --date "$TARGET_DATE"
  python automation/enrich_all_venues_kimarite.py --date "$TARGET_DATE"
  python automation/apply_toda_v5.py --date "$TARGET_DATE"
  python automation/apply_heiwajima_v1.py --date "$TARGET_DATE"
  python automation/apply_karatsu_v1.py --date "$TARGET_DATE"
  python automation/apply_wakamatsu_v2_3.py --date "$TARGET_DATE"
  python automation/apply_omura_v1.py --date "$TARGET_DATE"
  python automation/run_ashiya_v16.py --date "$TARGET_DATE" --stage pre
  python automation/run_biwako_v1_2.py --date "$TARGET_DATE" --stage preliminary
  python automation/run_shimonoseki_v6_1.py --date "$TARGET_DATE" --stage preliminary
  python automation/run_fukuoka_v1.py --date "$TARGET_DATE" --stage preliminary
  python automation/run_tokoname_v1.py \
    --date "$TARGET_DATE" --stage preliminary --write
  if [ -f "data/venues/naruto/${TARGET_DATE//-/}.json" ]; then
    python automation/run_naruto_v5.py --date "$TARGET_DATE" --stage morning
  fi

  python automation/build_site_data.py --date "$TARGET_DATE" \
    | tee "build-after-engine-${log_suffix}.log"
  python automation/enrich_motor_recent.py --date "$TARGET_DATE"
  python automation/enrich_all_venues_kimarite.py --date "$TARGET_DATE"
  python automation/validate_morning_regression.py \
    --before "$before_dir" \
    --after data
}

python engines/tokoname_v1/backfill_lane1_fly_v1.py \
  2026-10-06 2026-10-07 2026-10-08 2026-10-09 2026-10-10 \
  --missing-only --write
cp -a data "$RUNNER_TEMP/morning-data-before"
python -m unittest discover -s tests
PYTHONPATH="engines/ashiya/ashiya_engine_v1_6${PYTHONPATH:+:$PYTHONPATH}" \
  python -m pytest -q \
    engines/ashiya/ashiya_engine_v1_6/tests \
    tests/test_ashiya_live_v42.py \
    tests/test_ashiya_fly_probability_stages.py \
    tests/test_ashiya_native_prediction_preservation.py \
    tests/test_ashiya_non_running.py \
    tests/test_ashiya_prediction_history.py \
    tests/test_ashiya_live_source_freshness.py
run_publication_pipeline "$RUNNER_TEMP/morning-data-before" "preflight"
rm -rf "$course_cache_dir"
cp -a data/player_course_6m "$course_cache_dir"

{
  echo "## Morning data report"
  echo '```json'
  cat data/morning_report.json
  echo '```'
} >> "$GITHUB_STEP_SUMMARY"

git config user.name "github-actions[bot]"
git config user.email "41898282+github-actions[bot]@users.noreply.github.com"

for attempt in 1 2 3 4 5; do
  echo "Publish attempt ${attempt}/5"
  git fetch origin main
  git reset --hard origin/main
  git clean -fd data
  rm -rf data/player_course_6m
  cp -a "$course_cache_dir" data/player_course_6m

  python engines/tokoname_v1/backfill_lane1_fly_v1.py \
    2026-10-06 2026-10-07 2026-10-08 2026-10-09 2026-10-10 \
    --missing-only --write

  before_publish="$RUNNER_TEMP/morning-data-before-publish"
  rm -rf "$before_publish"
  cp -a data "$before_publish"
  run_publication_pipeline "$before_publish" "sync-${attempt}"

  git add data ':(exclude)data/player_course_6m/player_course_6m.sqlite'
  if git diff --cached --quiet; then
    echo "No data changes."
    exit 0
  fi

  git commit -m "Update morning race data ${TARGET_DATE}"
  if git push origin HEAD:main; then
    echo "Morning data published successfully."
    exit 0
  fi

  echo "Push was rejected because main changed; rebuilding from latest main."
  git reset --hard origin/main
  if [ "$attempt" -lt 5 ]; then
    sleep $((attempt * 10))
  fi
done

echo "Morning data publish failed after 5 attempts." >&2
exit 1
