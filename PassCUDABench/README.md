# PassCUDABench

PassCUDABench is a benchmark for automated password change page navigation on real-world websites. It covers 100 live websites sampled from the Tranco Top-550, after excluding infrastructure, non-user-facing, and paid-registration domains. Sites span eight service categories: Productivity & Cloud SaaS, Media & Streaming, Social & Messaging, News & Information, E-commerce & Marketplace, Developer & Knowledge, Finance & Payments, and Travel & Hospitality.

Navigation paths were collected by running PassCUDA on each site and selecting the shortest successful trace. Each entry records the full action sequence from the site homepage to the password change interface, along with site-level authentication and anti-automation metadata.

Navigation paths average 8.42 steps (σ=2.25, median=8), ranging from 4 to 15. 35 sites enforce 2FA, 18 deploy CAPTCHA, and 29 employ anti-automation measures.


## Dataset format

The benchmark is provided as a single JSON file (`PassCUDABench_100.json`), a list of site objects.

```json
{
  "site_id": 4,
  "name": "apple",
  "url": "https://www.apple.com",
  "category": "Productivity & Cloud SaaS",
  "PW_change_page_url": "https://account.apple.com/account/manage",
  "difficulty_factors": {
    "requires_login": true,
    "has_2fa": true,
    "has_captcha": false,
    "anti_automation": false,
    "cross_origin_iframe": true
  },
  "known_path": [...],
  "on_page_actions": [...]
}
```

### Top-level fields

- `site_id`: integer index
- `name`: short identifier for the site
- `url`: homepage URL used as the navigation starting point
- `category`: service category
- `PW_change_page_url`: URL of the page where the user can enter their current and new password. This is the navigation target.
- `difficulty_factors`: site-level authentication and anti-automation characteristics (see below)
- `known_path`: action sequence from the homepage to `PW_change_page_url`
- `on_page_actions`: actions that must be performed after reaching `PW_change_page_url` without any further URL change, such as scrolling to a section or clicking a dynamically rendered entry that expands the password form


### Action fields

Each step in `known_path` and `on_page_actions` has the following fields:

- `step`: sequential index starting from 1, continuous across `known_path` and `on_page_actions`
- `action_type`: type of action to perform (see below)
- `element_text`: visible text of the target element as it appears on screen
- `icon_hint`: shape or semantic description of the target element when it has no visible text (e.g., `user`, `gear`, `lock`, `hamburger`)
- `element_html`: raw HTML string of the target element collected from the DOM. This is `null` when the action does not require a specific DOM element (e.g., `navigate`), or when the action was resolved via a DOM hint shortcut (PASSCUDA's element collection phase) rather than through screenshot-level matching
- `optional`: whether this action may not be needed depending on the environment or account state. When `true`, the `condition` field describes the context in which it appears (e.g., `cookie_banner`, `ad_popup`, `first_login`). Note that the reverse also holds: traces were collected under specific account and region conditions, so additional actions not recorded here may appear in other environments, such as notification prompts, consent dialogs, or account-state warnings
- `condition`: context in which an optional action appears. Values include `cookie_banner`, `user_consent`, `ad_popup`, `first_login`, `region_specific`, `2fa_email`, `2fa_sms`, `captcha`, and `anti_bot`. `null` for required steps

Where `element_text` or `element_html` would contain the test account's username or email address, the value is replaced with `[username]` or `[user email]` respectively.


### Action types

- `navigate`: direct URL navigation, not tied to a specific element
- `click`: click on a button, link, or other interactive element
- `type`: enter text into an input field
- `CAPTCHA`: complete a CAPTCHA challenge
- `scroll`: scroll the page to bring content into view
- `wait`: wait for a page load or action to complete


### difficulty_factors

- `requires_login`: whether the site requires authentication before the password change page is accessible
- `has_2fa`: whether the site enforces two-factor authentication (email or SMS) during login
- `has_captcha`: whether the site presents a CAPTCHA challenge during login or navigation
- `anti_automation`: whether the site deploys bot detection, including both explicit CAPTCHA and passive fingerprinting or risk-based authentication mechanisms
- `cross_origin_iframe`: whether any action target in the navigation path is rendered inside a cross-origin iframe. Browsers block DOM access to cross-origin iframe content under the same-origin policy, so element collection via JavaScript will return no candidates for those steps. This is relevant for agents that rely on DOM-based element extraction


## Notes on live websites

PassCUDABench targets live websites, not sandboxed environments. Navigation paths were recorded at a specific point in time and may become outdated as sites update their UI or account settings structure. When using this benchmark, note the collection date and re-verify paths that fail to reproduce.

Reproducing the full evaluation requires valid credentials for each site. Each account used during benchmark collection was a dummy account created solely for this purpose. Credentials are not included in this repository. You must provide your own test accounts.
