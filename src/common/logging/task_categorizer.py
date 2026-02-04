"""
Task Categorization for DOLOS-DEPLOY

Determines the appropriate StepCategory based on task content analysis.
Used by SmolAgents and BrowserUse workflows for dynamic categorization.

IMPORTANT: This categorizer always returns a specific category, never "other".
For web-based agents (SmolAgents, BrowserUse), default is "browser".
"""

import re
from typing import Optional


# Valid categories - note "other" is intentionally excluded from defaults
# to ensure tasks always get a meaningful category
VALID_CATEGORIES = ["browser", "video", "office", "shell", "programming", "email", "authentication"]

# Category keywords mapping
# Order matters - more specific categories should be checked first
CATEGORY_PATTERNS = {
    "video": [
        r"\byoutube\b",
        r"\bvideo\b",
        r"\bwatch\b",
        r"\bstreaming\b",
        r"\bvimeo\b",
        r"\btwitch\b",
        r"\bnetflix\b",
        r"\bmovie\b",
        r"\bclip\b",
        r"\btiktok\b",
        r"\breels?\b",
        r"\bshorts?\b",
        r"\bplaylist\b",
        r"\bchannel\b",  # YouTube channel
        r"\bsubscrib",  # subscribe/subscription
    ],
    "office": [
        r"\bdocument\b",
        r"\bword\b",
        r"\bspreadsheet\b",
        r"\bexcel\b",
        r"\bcalc\b",
        r"\bpowerpoint\b",
        r"\bpresentation\b",
        r"\bslides?\b",
        r"\bpdf\b",
        r"\bwrite\s+(a\s+)?(document|report|memo)\b",
        r"\bcreate\s+(a\s+)?(document|spreadsheet|presentation)\b",
        r"\bedit\s+(a\s+)?(document|file)\b",
        r"\boffice\b",
        r"\blibreoffice\b",
        r"\bopenoffice\b",
        r"\bgoogle\s+docs?\b",
        r"\bgoogle\s+sheets?\b",
        r"\bgoogle\s+slides?\b",
    ],
    "shell": [
        r"\bterminal\b",
        r"\bcommand\s*(line|prompt)?\b",
        r"\bshell\b",
        r"\bbash\s*(script|command)?\b",
        r"\bzsh\b",
        r"\bcli\b",
        r"\bshell\s+script\b",
        r"\bexecute\s+(a\s+)?command\b",
        r"\brun\s+(a\s+)?(command|shell)\b",
        r"\bsudo\b",
        r"\bapt(-get)?\b",
        r"\bpip\s+install\b",
        r"\bnpm\s+(install|run)\b",
        r"\bcurl\b",
        r"\bwget\b",
        r"\bgrep\b",
        r"\bawk\b",
        r"\bsed\b",
        r"\bls\b",
        r"\bcd\b",
        r"\bmkdir\b",
        r"\brm\b",
        r"\bcat\b",
    ],
    "programming": [
        r"\bcode\b",
        r"\bcoding\b",
        r"\bprogram(ming)?\b",
        r"\bcompile\b",
        r"\bbuild\s+(a\s+)?(project|app|application|software)\b",
        r"\bIDE\b",
        r"\bdebug(ging)?\b",
        r"\bgit\b",
        r"\brepository\b",
        r"\brepo\b",
        r"\bpull\s+request\b",
        r"\bcommit\b",
        r"\bvscode\b",
        r"\beditor\b",
        r"\bgithub\b",
        r"\bgitlab\b",
        r"\bbitbucket\b",
        r"\bstackoverflow\b",
        r"\bstack\s+overflow\b",
        r"\bapi\s+(endpoint|call|request)\b",
        r"\bdevelop\s+(a\s+)?(python|java|javascript|c\+\+|rust|go|ruby)?\s*(app|application|software|tool|project|program)\b",
        r"\bsoftware\s+(development|engineering|project)\b",
        r"\bwrite\s+(a\s+)?(script|code|program)\b",
        r"\bsource\s+code\b",
        r"\bfunction\b",
        r"\bclass\b",
        r"\bmethod\b",
        r"\bvariable\b",
        r"\bpython\s+(script|code|program|application)\b",
        r"\bjavascript\s+(code|program)\b",
    ],
    "email": [
        r"\bemail\b",
        r"\be-mail\b",
        r"\bmail\b",
        r"\binbox\b",
        r"\bgmail\b",
        r"\boutlook\b",
        r"\bsend\s+(a\s+)?message\b",
        r"\bcompose\b",
        r"\breply\s+to\b",
        r"\bforward\b",
        r"\battachment\b",
    ],
    "authentication": [
        r"\blogin\b",
        r"\blog\s+in\b",
        r"\bauthenticat(e|ion)\b",
        r"\bsign\s*(in|up|out)\b",
        r"\bpassword\b",
        r"\bcredential\b",
        r"\bsso\b",
        r"\boauth\b",
        r"\b2fa\b",
        r"\bmfa\b",
        r"\bverif(y|ication)\b",
        r"\bregister\b",
        r"\baccount\s+creat",
    ],
    # Browser patterns - checked last as fallback before default
    # These help catch general web browsing that isn't covered above
    "browser": [
        r"\bbrowse\b",
        r"\bweb\b",
        r"\bwebsite\b",
        r"\bvisit\b",
        r"\bnavigate\b",
        r"\bsearch\b",
        r"\bgoogle\b",
        r"\bbing\b",
        r"\bduckduckgo\b",
        r"\bwikipedia\b",
        r"\breddit\b",
        r"\btwitter\b",
        r"\bfacebook\b",
        r"\blinkedin\b",
        r"\bnews\b",
        r"\barticle\b",
        r"\bread\b",
        r"\bfind\b",
        r"\blook\s+up\b",
        r"\bresearch\b",
        r"\bdownload\b",
        r"\bclick\b",
        r"\blink\b",
        r"\bpage\b",
        r"\burl\b",
        r"\bhttp",
    ],
}


# Default mechanical step names for each category.
# Used as fallback when no specific action is detected from LLM responses.
# All frameworks (MCHP, BrowserUse, SmolAgents) share this vocabulary.
CATEGORY_STEP_NAMES = {
    "browser": "navigate",
    "video": "navigate",
    "office": "edit_content",
    "shell": "spawn_shell",
    "programming": "edit_content",
    "email": "navigate",
    "authentication": "navigate",
    "other": "navigate",
}


def step_name_for_category(category: str) -> str:
    """Get the default mechanical step name for a given category.

    Args:
        category: One of the StepCategory values (browser, video, shell, etc.)

    Returns:
        Mechanical step name (navigate, edit_content, spawn_shell, etc.)
    """
    return CATEGORY_STEP_NAMES.get(category, "navigate")


def categorize_task(task: str, default: str = "browser") -> str:
    """
    Determine the category for a given task based on content analysis.

    IMPORTANT: This function always returns a specific category, never "other".
    For tasks that don't match specific patterns, it defaults to "browser"
    since SmolAgents and BrowserUse are primarily web-based agents.

    Args:
        task: The task description/prompt to analyze
        default: Default category if no patterns match (default: "browser")
                 Should be one of the VALID_CATEGORIES

    Returns:
        One of: browser, video, office, shell, programming, email, authentication
        Never returns "other" - will use the default instead
    """
    if not task:
        return default if default in VALID_CATEGORIES else "browser"

    task_lower = task.lower()

    # Check each category's patterns (video, office, shell, etc. first, browser last)
    for category, patterns in CATEGORY_PATTERNS.items():
        for pattern in patterns:
            if re.search(pattern, task_lower):
                return category

    # Return the default, ensuring it's valid (never "other")
    return default if default in VALID_CATEGORIES else "browser"


def get_category_for_url(url: str) -> str:
    """
    Determine the category based on URL patterns.

    IMPORTANT: Always returns a specific category, never "other".

    Args:
        url: The URL being accessed

    Returns:
        One of: browser, video, office, shell, programming, email, authentication
    """
    if not url:
        return "browser"

    url_lower = url.lower()

    # Video sites
    video_domains = ["youtube.com", "youtu.be", "vimeo.com", "twitch.tv", "netflix.com",
                     "dailymotion.com", "tiktok.com", "hulu.com", "disneyplus.com"]
    if any(domain in url_lower for domain in video_domains):
        return "video"

    # Email sites
    email_domains = ["gmail.com", "outlook.com", "mail.google.com", "mail.yahoo.com",
                     "protonmail.com", "fastmail.com", "zoho.com/mail"]
    if any(domain in url_lower for domain in email_domains):
        return "email"

    # Code/programming sites
    code_domains = ["github.com", "gitlab.com", "bitbucket.org", "stackoverflow.com",
                    "codepen.io", "replit.com", "codesandbox.io", "jsfiddle.net"]
    if any(domain in url_lower for domain in code_domains):
        return "programming"

    # Office/document sites
    office_domains = ["docs.google.com", "sheets.google.com", "slides.google.com",
                      "office.com", "onedrive.com", "dropbox.com/paper", "notion.so"]
    if any(domain in url_lower for domain in office_domains):
        return "office"

    # Authentication pages (common patterns)
    auth_patterns = ["/login", "/signin", "/sign-in", "/auth", "/oauth", "/sso", "/account"]
    if any(pattern in url_lower for pattern in auth_patterns):
        return "authentication"

    # Default to browser for all other web URLs
    return "browser"
