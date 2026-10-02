# PROJECT BRIEF — PriceWise

## Problem Statement
Tracking grocery and household purchase prices over time is tedious. Paper receipts pile up, manual entry is error-prone, and there is no easy way to know if a quoted price is fair based on past spending.

## Solution Built
A Telegram bot that accepts receipt photos or PDFs and auto-extracts all line items using dual OCR + LLM parsing (Claude or Ollama). Users review items with a single tap, save them to Google Sheets, and can query price history or check whether a deal is good.

## Key Features
- Dual OCR pipeline for high accuracy
- Inline item editing before saving
- `/best <item>` — price history (min / max / avg)
- `/deal <price> <item>` — good / fair / expensive verdict
- Text input fallback ("Bought eggs ₹60 at DMart")

## Tech Stack
Python · Telegram Bot API · Claude Sonnet / Ollama · Google Sheets OAuth · pytesseract / pdf2image

## Status
MVP Done

## Vibe Coding Effort
Sessions: ___ | Est. Hours: ___

## Users & Signups
Active Users: ___ | Total Signups: ___

## Growth Tracking
_Add notes on user growth, usage frequency, retention here._

## Last Updated
2026-02 (Feb 2026)

## Key Files
- `bot.py` — main Telegram bot
- `google_auth.py` — Google Sheets OAuth helper
- `requirements.txt` — dependencies
