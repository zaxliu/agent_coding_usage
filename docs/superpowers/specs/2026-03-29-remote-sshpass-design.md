# Archived Spec

This historical design doc has been superseded by the current remote authentication model:

- try SSH keys first
- if authentication fails, prompt for a password
- execute password-based retries through Paramiko
- keep passwords in memory for the active session only
