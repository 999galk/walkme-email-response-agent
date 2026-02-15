# WalkMe Email Response Agent
Home assignment for the WalkMe AI Solution Engineer position.

A CLI agent that:

1. Searches Gmail using natural language
2. Shows up to the last 5 matching emails
3. Lets the user choose the correct thread (or re-search)
4. Displays the latest message in that thread
5. Generates a reply using OpenAI
6. Requires user approval (edit/regenerate/cancel)
7. Supports preview-to-self (dry run)
8. Runs safety checks before external send
9. Sends only after explicit confirmation

The goal is to demonstrate practical AI-assisted automation with:

- safe credential handling
- human-in-the-loop controls
- failure-aware design
- reproducible runtime setup

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
   e.g. `"project proposal follow-up"`

2) Agent converts text → Gmail query

3) Up to **5 matching emails are shown**

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
Reference: 123456789012
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

## Key libraries

- `google-api-python-client`, `google-auth`, `google-auth-oauthlib`  
  Gmail OAuth + API access

- `python-dotenv`  
  Local secret management

- `openai`  
  Draft reply generation

---

## Repository structure

```
main.py         → CLI orchestration
gmail_client.py → Gmail OAuth/search/parsing/sending
llm_client.py   → OpenAI wrapper
agent.py        → approval loop + safety logic
```

---

## Troubleshooting

### Browser does not open

Check:

- `credentials.json` exists in repo root
- `.env` paths are correct

### Need to re-auth Gmail

```bash
rm token.json
python main.py
```

### Authentication 403 error

Add your Gmail under:

```
OAuth consent screen → Test users
```

---

## Python version note

Developed with Python 3.12.  
Python 3.10+ recommended.

Older Python versions may show deprecation warnings from Google libraries.
