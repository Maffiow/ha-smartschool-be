"""Constants for the Smartschool BE integration."""

DOMAIN = "smartschool_be"
CONF_BASE_URL = "base_url"
CONF_BIRTHDAY = "birthday"
CONF_TOTP_SECRET = "totp_secret"
# Form-only: a 6-digit authenticator code, used once and never stored.
CONF_TOTP_CODE = "totp_code"
DEFAULT_SCAN_INTERVAL_MINUTES = 15
MESSAGES_SCAN_INTERVAL_MINUTES = 5
AGENDA_SCAN_INTERVAL_MINUTES = 60
AGENDA_DAYS_AHEAD = 13

EVENT_NEW_MESSAGE = f"{DOMAIN}_new_message"

ATTR_CONFIG_ENTRY_ID = "config_entry_id"
ATTR_MESSAGE_ID = "message_id"
ATTR_MESSAGE = "message"
ATTR_SUBJECT = "subject"
ATTR_REPLY_ALL = "reply_all"
ATTR_RECIPIENT = "recipient"
ATTR_FLAG = "flag"
PLANNER_DAYS_AHEAD = 30

# Options: which Smartschool modules to use (all on by default).
CONF_RESULTS = "enable_results"
CONF_MESSAGES = "enable_messages"
CONF_AGENDA = "enable_agenda"
CONF_PLANNER = "enable_planner"
CONF_STUDENTS = "enable_students"
MODULE_OPTIONS = (CONF_RESULTS, CONF_MESSAGES, CONF_AGENDA, CONF_PLANNER, CONF_STUDENTS)

SERVICE_SELECT_MESSAGE = "select_message"
# Dispatcher signal (format with the config entry id): a message was opened.
SIGNAL_SELECTED_MESSAGE = f"{DOMAIN}_selected_message_{{}}"
FRONTEND_URL = f"/{DOMAIN}/smartschool-be.js"
