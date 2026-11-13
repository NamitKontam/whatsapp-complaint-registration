# WhatsApp Complaint Registration System

## 1. Project overview

Build a WhatsApp-based complaint registration system. A person sends a
message to a WhatsApp Business number, describes a complaint, and can
add supporting information using text, images, audio, or a location. The
system collects all messages into one complaint and confirms submission
when the person says they have no more information to add.

The project will be developed incrementally, with each phase understood,
implemented, and tested before moving to the next. This document is the
shared project brief and starting point for future work.

## 2. Goals

-   Receive inbound WhatsApp messages through the Meta WhatsApp Cloud
    API.
-   Start a complaint conversation when a user sends `Hi` (and support
    sensible restart/help commands).
-   Accept complaint information as text, image, audio, or location.
-   Ask whether the user wants to provide more information.
-   If the user answers **Yes**, collect another message or attachment
    and ask again.
-   If the user answers **No**, submit the complaint and send a
    confirmation with a complaint reference number.
-   Keep every message and attachment associated with the same
    complaint.
-   Build a foundation that can later support an administrator dashboard
    for reviewing and managing complaints.

## 3. Proposed technology stack

  Area                               Technology
  ---------------------------------- ----------------------------------------------
  Messaging                          Meta WhatsApp Cloud API
  Developer setup                    Meta for Developers
  Backend                            Python, FastAPI
  Database                           PostgreSQL
  ORM                                SQLAlchemy
  Migrations                         Alembic
  Local webhook exposure             ngrok
  API testing                        Postman / curl
  Configuration                      Environment variables (`.env`)
  Media storage during development   Local storage
  Production media storage           Object storage (to be selected later)
  Background jobs                    Optional Celery + Redis, only when justified

Start with a small, understandable implementation. Do not introduce
Celery, Redis, a frontend, or cloud infrastructure before the core
webhook and conversation flow work.

## 4. User conversation flow

### Initial conversation

1.  User sends `Hi`.
2.  The backend identifies the WhatsApp sender and starts or resumes a
    complaint conversation.
3.  The bot welcomes the user and asks them to describe the complaint.
4.  The user sends one or more supported message types:
    -   Text
    -   Image (optionally with a caption)
    -   Audio
    -   Location (latitude and longitude)
5.  The bot asks: **"Would you like to provide any additional
    information?"**
6.  The user chooses Yes or No.

### If the user chooses Yes

1.  Ask the user to send additional information.
2.  Accept text, image, audio, or location.
3.  Attach the new message to the current complaint.
4.  Ask again whether they want to provide more information.
5.  Repeat until the user chooses No.

### If the user chooses No

1.  Mark the complaint as submitted.
2.  Generate a unique, human-readable complaint reference, for example
    `CMP-000001`.
3.  Record the submission time.
4.  Send a confirmation, for example:

> Thank you. Your complaint has been successfully registered. Our team
> will review it and contact you shortly. Your complaint reference
> number is CMP-000001.

### Conversation preview

``` text
User: Hi

Bot: Hello! Welcome to our Complaint Registration System.
Please describe your complaint. You can send text, a photo, audio,
or your location.

User: There is water leakage near my house.

Bot: Thank you for sharing the details.
Would you like to provide any additional information?

User: Yes

Bot: Sure. Please send any additional details, photo, audio, or location.

User: It is opposite the main school gate.

Bot: Would you like to provide any more information?

User: No

Bot: Thank you. Your complaint has been registered.
Our team will review it and contact you shortly.
Your reference number is CMP-000001.
```

## 5. Conversation state machine

Use an explicit conversation state machine rather than a large
collection of loosely connected `if/else` statements.

  ---------------------------------------------------------------------------------
  State                        Expected input          Action / next state
  ---------------------------- ----------------------- ----------------------------
  `IDLE`                       `Hi` / start command    Create or resume a draft
                                                       complaint; move to
                                                       `AWAITING_COMPLAINT`

  `AWAITING_COMPLAINT`         Text, image, audio,     Save message; move to
                               location                `ASKING_FOR_MORE_INFO`

  `ASKING_FOR_MORE_INFO`       Yes                     Move to
                                                       `AWAITING_ADDITIONAL_INFO`

  `ASKING_FOR_MORE_INFO`       No                      Submit complaint; move to
                                                       `COMPLETED`

  `AWAITING_ADDITIONAL_INFO`   Text, image, audio,     Save message; move to
                               location                `ASKING_FOR_MORE_INFO`

  `COMPLETED`                  New `Hi` / start        Start a new complaint
                               command                 conversation
  ---------------------------------------------------------------------------------

The exact handling of unexpected input, cancellation, restart, and
abandoned drafts will be designed and tested during implementation.

Prefer WhatsApp interactive reply buttons for Yes/No when supported by
the chosen API flow. Also handle typed replies such as `yes`, `y`, `no`,
and `n`, because users may type instead of pressing a button.

Do not create a new complaint for every message. A complaint is one
parent record with multiple associated messages.

## 6. High-level architecture

``` text
User's WhatsApp
      |
      v
WhatsApp Cloud API (Meta)
      |
      | HTTPS webhook event
      v
ngrok public HTTPS URL
      |
      v
FastAPI webhook endpoint
      |
      v
Webhook validation and message parsing
      |
      v
Conversation / state-machine service
      |
      +----------------------+
      |                      |
      v                      v
PostgreSQL              WhatsApp Cloud API
(users, complaints,     (send replies,
messages, states)        buttons and confirmations)
      |
      v
Media storage
(local during development; object storage in production)
```

### Why ngrok is needed

During local development, FastAPI may run at `http://localhost:8000`.
Meta's servers cannot call a developer's localhost directly. ngrok
exposes the local server through a public HTTPS URL, such as:

``` text
https://example.ngrok-free.app
```

The webhook callback URL will look like:

``` text
https://example.ngrok-free.app/api/webhooks/whatsapp
```

The ngrok URL may change after restarting a free tunnel. Update the Meta
callback URL when that happens, or use a reserved domain if available.

## 7. WhatsApp webhook responsibilities

Use one route for both webhook verification and inbound events:

``` text
GET  /api/webhooks/whatsapp
POST /api/webhooks/whatsapp
```

### GET: webhook verification

Meta sends verification query parameters. The endpoint should:

-   Check the expected verification mode.
-   Compare the supplied verify token with the configured token.
-   Return the challenge only when verification succeeds.
-   Reject invalid verification requests.

### POST: inbound events

The endpoint should:

-   Verify the `X-Hub-Signature-256` signature using the Meta App
    Secret.
-   Parse the webhook payload safely.
-   Identify the sender and WhatsApp message ID.
-   Identify the message type.
-   Pass the event to the appropriate application service.
-   Return a successful response promptly.
-   Avoid processing duplicate message deliveries.

Keep the HTTP route thin. Put conversation and complaint logic in
service modules.

## 8. Message and media handling

The webhook payload for media generally contains a media ID and
metadata, not the media file itself.

  -----------------------------------------------------------------------
  Type                    Data to capture         Processing
  ----------------------- ----------------------- -----------------------
  Text                    Message body            Save text

  Image                   Media ID, MIME type,    Retrieve media URL,
                          caption if present      download with
                                                  authorized API request,
                                                  validate and store

  Audio                   Media ID, MIME type     Retrieve and download,
                                                  validate and store

  Location                Latitude, longitude,    Save coordinates and
                          optional name/address   optional descriptive
                                                  fields
  -----------------------------------------------------------------------

Important implementation details:

-   Treat media IDs as external identifiers, not permanent file URLs.
-   Download media through the authenticated WhatsApp media API flow.
-   Validate MIME type, file size, and file contents.
-   Generate safe server-side filenames; do not trust user-supplied
    filenames.
-   Store media outside publicly served application directories.
-   Store media metadata and a storage key/path in the database.
-   Do not expose access tokens or private media URLs in logs or API
    responses.
-   Decide later whether audio should simply be retained or transcribed.
    Audio transcription is not required for the first version.

## 9. Initial database design

Start with three main tables. Refine fields and constraints before
writing migrations.

### `users`

Stores people who contact the WhatsApp number.

  -----------------------------------------------------------------------
  Column                  Suggested type          Notes
  ----------------------- ----------------------- -----------------------
  `id`                    UUID                    Primary key

  `whatsapp_number`       String                  Unique sender number in
                                                  normalized format

  `name`                  String, nullable        Name if available from
                                                  the webhook/profile

  `created_at`            DateTime                UTC timestamp
  -----------------------------------------------------------------------

### `complaints`

Stores one complaint conversation and its lifecycle.

  Column                 Suggested type       Notes
  ---------------------- -------------------- ---------------------------------------------------
  `id`                   UUID                 Primary key
  `complaint_number`     String               Unique public reference
  `user_id`              UUID                 Foreign key to users
  `status`               Enum                 Draft/submitted and later administrative statuses
  `conversation_state`   Enum                 Current conversation state
  `created_at`           DateTime             UTC timestamp
  `completed_at`         DateTime, nullable   When submitted

Suggested complaint lifecycle statuses:

-   `DRAFT`
-   `SUBMITTED`
-   `UNDER_REVIEW`
-   `RESOLVED`
-   `REJECTED`

Conversation state and complaint status are different concepts. For
example, a submitted complaint may later move from `UNDER_REVIEW` to
`RESOLVED`, while the conversation itself remains `COMPLETED`.

### `complaint_messages`

Stores every inbound message associated with a complaint.

  ---------------------------------------------------------------------------------
  Column                  Suggested type          Notes
  ----------------------- ----------------------- ---------------------------------
  `id`                    UUID                    Primary key

  `complaint_id`          UUID                    Foreign key to complaints

  `whatsapp_message_id`   String                  Unique external message ID for
                                                  idempotency

  `message_type`          Enum                    Text/image/audio/location/other

  `text_content`          Text, nullable          Text or caption

  `media_id`              String, nullable        WhatsApp media identifier

  `media_mime_type`       String, nullable        Validated MIME type

  `media_storage_key`     String, nullable        Local path initially; storage key
                                                  later

  `latitude`              Numeric, nullable       Location latitude

  `longitude`             Numeric, nullable       Location longitude

  `created_at`            DateTime                UTC timestamp
  ---------------------------------------------------------------------------------

A user may send several messages for one complaint. Keep them as
separate rows so the complete complaint history can be displayed later.

Consider adding a separate webhook-event or outbound-message table if
delivery tracking, retries, and operational auditing require it. Do not
add tables without a clear use case.

## 10. Suggested backend structure

A starting structure could look like this. Adjust it as the application
grows; avoid creating empty modules just to match a diagram.

``` text
complaint-system/
├── backend/
│   ├── app/
│   │   ├── api/
│   │   │   └── routes/
│   │   │       └── whatsapp_webhook.py
│   │   ├── core/
│   │   │   ├── config.py
│   │   │   └── security.py
│   │   ├── db/
│   │   │   ├── base.py
│   │   │   └── session.py
│   │   ├── models/
│   │   │   ├── user.py
│   │   │   ├── complaint.py
│   │   │   └── complaint_message.py
│   │   ├── schemas/
│   │   └── services/
│   │       ├── whatsapp_service.py
│   │       ├── conversation_service.py
│   │       ├── complaint_service.py
│   │       └── media_service.py
│   ├── alembic/
│   ├── tests/
│   ├── .env.example
│   ├── .gitignore
│   └── requirements.txt
├── docs/
│   └── PROJECT_PLAN.md
└── README.md
```

## 11. Environment configuration

Keep credentials in environment variables. A future `.env.example` may
include placeholders such as:

``` dotenv
APP_ENV=development
DATABASE_URL=postgresql+psycopg://user:password@localhost:5432/complaints_db

WHATSAPP_ACCESS_TOKEN=
WHATSAPP_PHONE_NUMBER_ID=
WHATSAPP_BUSINESS_ACCOUNT_ID=
WHATSAPP_API_VERSION=

META_APP_SECRET=
WHATSAPP_VERIFY_TOKEN=

MEDIA_STORAGE_DIR=./storage/media
```

Use the API version currently supported by Meta when configuring the
application. Never commit a real `.env` file, access token, App Secret,
or verification token.

## 12. Reliability and security requirements

Build these into the design rather than treating them as optional
cleanup:

1.  **Webhook idempotency:** Meta may retry event delivery. Enforce
    uniqueness on the WhatsApp message ID and make repeated processing
    safe.
2.  **Signature verification:** Verify inbound webhook signatures using
    the raw request body and Meta App Secret.
3.  **Prompt acknowledgement:** Acknowledge webhook delivery promptly.
    Avoid long media downloads or slow processing inside the request
    handler as the system grows.
4.  **Conversation isolation:** Resolve conversations by the sender's
    WhatsApp identity and ensure one user's messages cannot attach to
    another user's complaint.
5.  **Concurrent messages:** Consider two messages arriving close
    together. Use transactions and appropriate locking or concurrency
    controls around conversation state.
6.  **Input validation:** Validate message structures, coordinates, text
    lengths, media MIME types, and media sizes.
7.  **Secrets management:** Never log or expose tokens, App Secrets, or
    sensitive message content unnecessarily.
8.  **Error handling:** Record failures and make outbound retries safe.
    Do not blindly resend messages when the outcome is uncertain.
9.  **Abandoned drafts:** Define how long an incomplete complaint
    remains active and what happens when the user returns later.
10. **Privacy and retention:** Define who can access complaints and
    attachments, how long they are retained, and how deletion requests
    are handled.

## 13. WhatsApp platform considerations

-   Use the WhatsApp Cloud API test number first.
-   Add permitted test recipients in the Meta developer setup.
-   Follow Meta's current rules for the customer-service messaging
    window and approved message templates.
-   Interactive reply buttons have platform constraints; confirm the
    current supported format and limits before implementation.
-   Production use may require business verification, a production phone
    number, payment/billing setup, and compliance with WhatsApp
    policies.
-   Do not assume that a development test number behaves exactly like a
    production number.

Official documentation:

-   Meta for Developers: https://developers.facebook.com/
-   WhatsApp Cloud API getting started:
    https://developers.facebook.com/docs/whatsapp/cloud-api/get-started
-   WhatsApp Cloud API reference:
    https://developers.facebook.com/docs/whatsapp/cloud-api/reference
-   WhatsApp media API:
    https://developers.facebook.com/docs/whatsapp/cloud-api/reference/media
-   ngrok: https://ngrok.com/download

Always check current Meta documentation for the latest setup screens,
API version, permissions, and messaging rules.

## 14. Implementation roadmap and milestones

### Phase 1 --- Meta account and WhatsApp setup

-   [ ] Register/log in to Meta for Developers.
-   [ ] Create a Meta application.
-   [ ] Add and configure the WhatsApp product.
-   [ ] Obtain a test phone number.
-   [ ] Add a test recipient.
-   [ ] Obtain the temporary access token, Phone Number ID, and WhatsApp
    Business Account ID.
-   [ ] Send a test message using the Cloud API.
-   [ ] Understand which credentials are temporary and how production
    credentials will be managed.

**Exit condition:** A test message can be sent successfully through the
WhatsApp Cloud API.

### Phase 2 --- FastAPI foundation and ngrok

-   [ ] Create the repository and Python virtual environment.
-   [ ] Install and configure FastAPI.
-   [ ] Add settings and environment-variable loading.
-   [ ] Add a health-check endpoint.
-   [ ] Start the application locally.
-   [ ] Install and authenticate ngrok.
-   [ ] Expose port 8000 through HTTPS.
-   [ ] Implement the webhook GET verification endpoint.
-   [ ] Configure the callback URL and verify token in Meta.
-   [ ] Subscribe to the `messages` webhook field.

**Exit condition:** Meta successfully verifies the webhook URL.

### Phase 3 --- Receive and send WhatsApp messages

-   [ ] Implement the POST webhook endpoint.
-   [ ] Inspect and understand real inbound text-message payloads.
-   [ ] Verify webhook signatures.
-   [ ] Parse sender, message ID, timestamp, and text.
-   [ ] Implement an outbound WhatsApp messaging service.
-   [ ] Reply to a user's `Hi`.
-   [ ] Test typed Yes/No replies and interactive buttons.

**Exit condition:** A message sent from WhatsApp reaches FastAPI and
receives an automated response.

### Phase 4 --- Database and complaint state machine

-   [ ] Configure PostgreSQL and SQLAlchemy.
-   [ ] Create user, complaint, and complaint-message models.
-   [ ] Configure Alembic migrations.
-   [ ] Implement user lookup/creation.
-   [ ] Implement complaint creation and reference-number generation.
-   [ ] Implement conversation-state transitions.
-   [ ] Save multiple messages under one complaint.
-   [ ] Submit the complaint when the user chooses No.
-   [ ] Add unit and integration tests for conversation paths.

**Exit condition:** A complete text-only complaint can be submitted and
retrieved from the database.

### Phase 5 --- Images, audio, and locations

-   [ ] Parse image messages and captions.
-   [ ] Retrieve and download image media.
-   [ ] Parse and download audio media.
-   [ ] Parse location messages and save coordinates.
-   [ ] Validate media types and sizes.
-   [ ] Save attachment metadata and storage references.
-   [ ] Test mixed message types in one complaint.

**Exit condition:** One complaint can contain text, images, audio, and
location messages.

### Phase 6 --- Reliability and edge cases

-   [ ] Prevent duplicate webhook processing.
-   [ ] Handle unexpected messages and invalid Yes/No responses.
-   [ ] Handle abandoned and resumed conversations.
-   [ ] Handle users starting a new complaint after completing one.
-   [ ] Handle WhatsApp API errors and outbound delivery failures.
-   [ ] Test concurrent messages and multiple users.
-   [ ] Add structured logs without leaking secrets.

**Exit condition:** The core flow behaves predictably under retries,
errors, and multiple users.

### Phase 7 --- Optional administrator dashboard and deployment

-   [ ] Decide administrator roles and permissions.
-   [ ] Build complaint list and detail views.
-   [ ] Display message history and attachments securely.
-   [ ] Display locations on a map if needed.
-   [ ] Allow authorized staff to update complaint status.
-   [ ] Choose production hosting and media storage.
-   [ ] Configure production secrets, HTTPS, monitoring, and backups.
-   [ ] Complete production WhatsApp onboarding and testing.

**Exit condition:** Authorized staff can review and manage submitted
complaints in a deployed environment.

## 15. How we will work through this project

Use these working rules in future project conversations:

-   Work phase by phase. Do not skip ahead unless there is a reason.
-   Explain the purpose of each component before implementing it.
-   Explain request/response payloads and the path a message takes
    through the application.
-   Prefer small, testable changes over generating the entire
    application at once.
-   After each implementation step, run the relevant tests or manual
    verification.
-   Explain errors and debugging steps instead of only supplying a
    replacement code block.
-   Keep architecture and documentation updated when project decisions
    change.
-   Do not invent successful Meta setup, webhook delivery, or API
    results; verify them with actual tests.
-   Keep secrets out of chat messages, screenshots, source control, and
    logs.

## 16. Current project status

**Status: Planning / setup not yet verified.**

The project requirements and proposed architecture are documented. No
Meta account, app, test number, webhook, ngrok tunnel, database, or
backend implementation should be considered complete until it is
actually configured and tested.

**Next action:** Register or log in to Meta for Developers and create
the developer application. Then configure the WhatsApp product and
obtain the test credentials. Continue with Phase 1 before writing the
webhook implementation.
