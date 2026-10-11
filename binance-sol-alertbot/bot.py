name: Crypto 15m Strategy Bot

on:
  schedule:
    - cron: '*/15 * * * *'
  workflow_dispatch:

jobs:
  run-bot:
    runs-on: ubuntu-latest

    steps:
      - name: Checkout repository
        uses: actions/checkout@v4

      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: '3.10'

      - name: Install dependencies
        run: |
          python -m pip install --upgrade pip
          pip install ccxt pandas numpy requests

      - name: Run trading strategy script
        run: python binance-sol-alertbot/bot.py
        env:
          BINANCE_API_KEY: ${{ secrets.BINANCE_API_KEY }}
          BINANCE_SECRET_KEY: ${{ secrets.BINANCE_SECRET_KEY }}
          TELEGRAM_TOKEN: ${{ secrets.TELEGRAM_TOKEN }}
          TELEGRAM_CHAT_ID: ${{ secrets.TELEGRAM_CHAT_ID }}

      - name: Commit and push updated bot state
        run: |
          git config --global user.name 'GitHub Actions Bot'
          git config --global user.email 'actions@github.com'
          git add binance-sol-alertbot/bot_state.json || true
          git diff --quiet && git diff --staged --quiet || (git commit -m "Auto-update bot state" && git push)
