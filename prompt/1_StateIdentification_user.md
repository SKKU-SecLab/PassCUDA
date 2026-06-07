You are given:
1) A webpage screenshot (current state)
2) An action trace representing actions already EXECUTED along the current navigation path
3) The rationale associated with the MOST RECENT action

ACTION_TRACE:
<<ACTION_TRACE>>

LAST_ACTION_RATIONALE:
<<ACTION_RATIONALE>>

--------------------------------------------------
STEP SELECTION
--------------------------------------------------

Select EXACTLY ONE state from the list below.

Valid states:

1. Navigate to the Login Page
   - The user is not authenticated.
   - The current screen is not a login page.

2. Perform Authentication
   - Authentication has not been completed.
   - This includes all REQUIRED authentication states:
     * entering username/email and password
     * mandatory 2FA or verification codes
   - Account or password settings are inaccessible until this state completes.

3. Navigate to Password Change Page
   - The user is authenticated. (If no explicit failure is visible, assume prior authentication succeeded.)
   - The password change or account security page has not yet been reached.

4. Dismiss or Skip OPTIONAL Blocking Interstitial
   - A non-mandatory modal, overlay, or UI element interrupts navigation.
   - This includes BOTH:
     (A) Explicit interaction blocking:
         * Clicks or typing are prevented until dismissed
     (B) Visual blocking signals that imply disabled background interaction:
         * Dimmed, greyed-out, or blurred background
         * Foreground banner/modal with backdrop overlay
         * Any UI state where the main page appears inactive or de-emphasized
   - The interruption can be skipped or dismissed (e.g., "Accept", "Skip", "Not now").
   - This state MUST NOT include mandatory authentication.

5. New Password Entry Stage Reached

   This state MUST be selected ONLY if at least ONE of the following is TRUE:
   - (A) NEW PASSWORD INPUT STATE
      - The interface explicitly presents fields for setting a NEW password. (e.g., "New password", "Create password", "Confirm password", or two distinct password fields indicating new + confirmation)

   - (B) PASSWORD UPDATE TRIGGERED STATE
      - A password update/reset action has ALREADY BEEN TRIGGERED by the user, and the interface indicates that the request has been dispatched or the next state has moved OUTSIDE the current page.
      - Examples: "We sent you a verification code", "Check your email", "SMS sent", "Verification link sent", a transition to a code-entry screen, a clear success/dispatch notification AFTER pressing a password-related button.
      - IMPORTANT: The trigger action MUST have already been executed. The presence of such a button ALONE is NOT sufficient.

6. Complete CAPTCHA or Bot-Detection Challenge
   - A human-verification or bot-detection challenge is visibly present AND currently UNSATISFIED or BLOCKING progress.
   - Includes any form of automated-abuse prevention mechanism, regardless of interaction modality, challenge structure, or presentation style.
   - MUST NOT be selected if the challenge is visibly completed or no longer blocks interaction.
   - Mere presence of a CAPTCHA widget does NOT imply State 6 is required.

--------------------------------------------------
DOM SEARCH SIGNAL GENERATION
--------------------------------------------------

After selecting the state, generate a prioritized list of text keywords for HTML/DOM searching.

The keywords MUST:
- Be directly aligned with the selected state.
- If a keyword corresponds EXACTLY to text visibly present in the current webpage, preserve the original visible text exactly as shown (including language).
- Be written in English if GENERATED, INFERRED, or ABSTRACTED.
- Not include generic or irrelevant navigation terms.

Each keyword MUST include a weight (1–10):
- 9–10: Strong and highly specific signal
- 6–8: Likely intermediate navigation signal
- 3–5: Weak but contextually relevant signal
- 1–2: Low-confidence exploratory signal

Keywords MUST reflect the CURRENT navigation stage, not the final objective unless State 3 or State 5 is selected.

--------------------------------------------------
OUTPUT FORMAT
--------------------------------------------------

{
  "state": integer,
  "decision": "continue|prune",
  "rationale": "one concise sentence explaining the state selection and decision",
  "search_keywords": [
    {"text": "string", "weight": integer}
  ]
}