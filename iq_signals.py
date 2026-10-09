name: iq-signals
on:
  schedule:
    - cron: "5 * * * 1-5"
  workflow_dispatch:

concurrency:
  group: iq-signals
  cancel-in-progress: false

jobs:
  scan:
    runs-on: ubuntu-latest
    timeout-minutes: 70
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - run: pip install requests
      - name: Test message (manual runs only)
        if: github.event_name == 'workflow_dispatch'
        run: python -c "from iq_signals import send; send('Test: channel connected. Signals will post here.')"
        env:
          TELEGRAM_TOKEN: ${{ secrets.TELEGRAM_TOKEN }}
          TELEGRAM_CHAT_ID: ${{ secrets.TELEGRAM_CHAT_ID }}
      - name: Scan every 5 minutes
        run: |
          end=$((SECONDS + 3300))
          while [ $SECONDS -lt $end ]; do
            h=$(date -u +%-H)
            if [ "$h" -ge 7 ] && [ "$h" -lt 16 ]; then
              python iq_signals.py || true
            fi
            sleep 285
          done
        env:
          TWELVE_DATA_KEY: ${{ secrets.TWELVE_DATA_KEY }}
          TELEGRAM_TOKEN: ${{ secrets.TELEGRAM_TOKEN }}
          TELEGRAM_CHAT_ID: ${{ secrets.TELEGRAM_CHAT_ID }}
