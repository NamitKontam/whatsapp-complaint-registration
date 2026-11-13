# WhatsApp Complaint Registration System

A WhatsApp-based complaint registration system built using
Meta WhatsApp Cloud API and FastAPI.

## Technology Stack

- Python
- FastAPI
- PostgreSQL
- SQLAlchemy
- Alembic
- Meta WhatsApp Cloud API
- ngrok

## Project Goals

- Register complaints through WhatsApp.
- Accept text, images, audio and location.
- Maintain conversation state.
- Generate unique complaint reference numbers.
- Store complaint history.
- Provide an administrator dashboard in a future phase.

## Development Roadmap

1. Meta and WhatsApp setup
2. FastAPI foundation and ngrok
3. WhatsApp webhook and messaging
4. Database and complaint state machine
5. Media and location handling
6. Reliability and edge cases
7. Admin dashboard and deployment

## Current Status

Phase 1 - Meta and WhatsApp setup.

See docs/PROJECT_PLAN.md for the complete specification.

## Development Principles

- Incremental implementation.
- Test before committing.
- Maintain clean Git history.
- Never commit credentials or secrets.
