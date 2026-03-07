# WalkMe Email Response Agent

Home assignment for the WalkMe AI Solution Engineer position.

This project is a CLI-based AI email assistant that can:

1. Understand which email the user wants to respond to
2. Search Gmail using natural language
3. Show matching threads and let the user choose the correct one
4. Load the latest message from the selected thread
5. Generate a reply draft using OpenAI
6. Keep a human in the loop for approval and editing
7. Allow preview-to-self before sending
8. Run safety checks before external send
9. Send only after explicit confirmation

---

## WHY THIS IS AGENTIC

The system is implemented as an LLM-orchestrated agent.

The LLM is responsible for deciding when to:

- convert natural language into a Gmail search query
- trigger Gmail search
- ask clarification questions when needed
- present candidate emails for selection

Once the correct email thread is selected, the flow becomes deterministic:

load thread → generate draft → human approval → safety checks → preview/send

This design demonstrates safe AI automation with real external systems.

---

## Requirements

- Python **3.10+ recommended** (tested with **3.12**)
- A Google account (for Gmail OAuth)
- An OpenAI API key

> Gmail access requires OAuth. API keys alone cannot read/send Gmail.

---

## Setup (step-by-step)

### 1) Create and activate a virtual environment

```bash
python -m venv .venv
source .venv/bin/activate   # mac/linux
# .venv\Scripts\activate    # windows
```

### 2) Install dependencies

```bash
pip install -r requirements.txt
```

### 3) Create environment file

```bash
cp .env.example .env
```

`.env` should include:

```env
OPENAI_API_KEY=your_key_here
GMAIL_CREDENTIALS_PATH=credentials.json
GMAIL_TOKEN_PATH=token.json
```

Secrets are excluded from git:

- `.env`
- `credentials.json`
- `token.json`

---

## OAuth test access

This project uses a Google OAuth app in **Testing mode**.

If authentication fails with:

```
Error 403: access_denied
```

it means your Gmail account is not yet added as a test user.

Please send me your Gmail address and I will add it under:

```
Google Cloud → OAuth consent screen → Test users
```

No Google Cloud setup is required on your side.

---

## First run behavior

On the first execution:

- browser opens to complete OAuth login
- `token.json` is created locally
- future runs reuse the token silently

To reset authentication:

```bash
rm token.json
python main.py
```

---

## Running the agent

```bash
python main.py
```

Flow:

1) Describe the email in natural language  
   e.g. `"walkme home assignment"`

2) Agent converts text → Gmail query

3) Up to **3 matching emails are shown from past 14 day**

4) User selects the correct thread  
   or chooses to re-search

5) Latest message in that thread is displayed

6) AI draft is generated

7) User can:
   - approve
   - edit manually
   - regenerate with optional instruction
   - cancel

8) After approval user chooses:
   - preview to self
   - send reply
   - keep editing
   - cancel

---

## Preview-to-self (dry run)

After approving a draft you can send a preview email to your own address.

This shows exactly how the reply will look in an inbox **without contacting the original sender**.

You can preview multiple times before sending externally.

---

## Safety checks before sending

Before sending externally, the agent runs guardrails to catch common AI failure modes:

- commitment / legal wording  
  (`guarantee`, `refund`, `approved`, `I confirm`, etc.)

- links in the draft

- original email contains questions but draft does not

- long number sequences (possible sensitive IDs)

If warnings exist, the user must type:

```
SEND
```

to proceed.

### How to test safety checks

Edit a draft and include one of:

```
https://example.com
I confirm this is approved.
payment : 123456789012
```

Then attempt to send — warnings should appear.

---

## LLM reply generation

The model is instructed to:

- stay concise and professional
- avoid hallucinated facts
- ask clarifying questions if needed
- produce draft-only output

Human approval is required before sending.

Regeneration supports optional user guidance:

```
more formal
shorter
ask about timeline
friendlier
```

---

## Human-in-the-loop architecture

AI proposes → user reviews → optional preview → explicit confirmation → send

This mirrors safe enterprise AI workflows where automation is gated by human authorization.

---

## Sending behavior / threading

Replies use Gmail API `users.messages.send` and preserve threading:

- reply to original sender
- include threadId
- add In-Reply-To / References headers

---

## Error handling

The agent converts Gmail/OpenAI failures into readable messages instead of crashing.

Examples handled:

- invalid OAuth credentials
- missing API key
- Gmail API errors
- invalid email addresses
- OpenAI service failure
- corrupted token file

The approval loop remains active so the user can retry or edit manually.

---

## TESTING SUPPORT

You can simulate failures to test error handling.

Example:

FORCE_DRAFT_ERROR=1 python main.py

This allows testing:

OpenAI draft failure  
fallback behavior

---

# PROJECT STRUCTURE

walkme-email-response-agent/

main.py
gmail_client.py
requirements.txt
README.md
.env.example

llm/
    client.py
    prompts.py

runtime/
    orchestrator.py
    state.py
    ui.py

tools/
    drafting.py
    gmail_tools.py
    query.py
    safety.py


## FILE RESPONSIBILITIES

### main.py  
Entry point of the application.

Initializes:
- Gmail client
- OpenAI client
- agent runtime

Then launches the CLI interaction loop.

### gmail_client.py  
Handles all Gmail API communication.

Responsibilities:

- OAuth authentication
- Gmail search queries
- thread retrieval
- message parsing
- preview email sending
- reply sending

This file isolates Google API logic from the agent runtime.

### llm/client.py  
Wrapper around the OpenAI SDK.

Used for:

- agent tool-calling turns
- reply draft generation

Includes explicit error handling for OpenAI failures.

### llm/prompts.py  
Contains the system prompt used by the agent.

Defines rules such as:

- search-first behavior
- limited clarification questions
- safety constraints
- draft formatting

### runtime/orchestrator.py  
Core agent runtime loop.

Responsible for:

- maintaining agent state
- executing tool calls
- managing search → select → draft → approve flow
- handling error recovery

### runtime/state.py  
Stores session state such as:

- search results
- selected thread
- generated draft
- user context

### runtime/ui.py  
CLI interaction layer.

Handles:

- candidate display
- selection menus
- approval loop
- regeneration prompts
- confirmation steps

### tools/query.py  
Converts natural language into Gmail search queries.

Includes logic for:

- sender detection
- keyword extraction
- default time windows
- fallback strategies

### tools/gmail_tools.py  
Structured wrappers around Gmail operations.

Examples:

- searching email threads
- fetching the latest message
- normalizing message output

### tools/drafting.py  
Handles reply generation with OpenAI.

Features:

- deterministic prompt construction
- regeneration support
- failure handling

If generation fails, the previous draft is preserved.

### tools/safety.py  
Runs pre-send safety checks.

Examples:

- commitment/legal wording
- links in the draft
- long number sequences
- system error text leakage
- unanswered questions

If warnings exist, explicit confirmation is required before sending.

---

## Troubleshooting


### Missing Gmail credentials

Error:

Missing Gmail OAuth credentials file

Fix:

Place credentials.json in the project root or update .env


### Reset Gmail authentication

rm token.json
python main.py


### OpenAI draft failure

Check:

API key is valid  
billing enabled  
FORCE_DRAFT_ERROR not set


### Preview/send failure

Possible causes:

Gmail authentication issue  
network interruption  
invalid thread state

The system will never claim an email was sent if the Gmail API fails.
