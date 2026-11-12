# Security model

PeopleBase holds pay and leave data, so who can see and change what is the
core of the design. This page names the trust boundaries, the roles, the access
rules and the abuse cases the code has to refuse. It was written before the
sign-in and role code, and every abuse case below has a test.

## Trust boundaries

| Boundary | What crosses it | Trust |
| --- | --- | --- |
| Browser to Flask app | URLs, query strings, form fields, the session cookie | Untrusted. Every value is validated, and identity comes only from the signed session cookie, never from a form field or URL. |
| Flask app to database | SQL built by SQLAlchemy with bound parameters | Trusted. The app is the only writer. Anyone with direct database access is outside this model. |
| Operator shell to app | `flask db upgrade`, `flask create-admin`, `flask seed-demo` | Trusted. Shell access to the host means full control. |
| GitHub Pages site | Static HTML and images | Separate origin with no data and no link to a running app. |

## Roles

Every account has exactly one role and may be linked to one employee record.

- **Employee.** Sees their own profile, pay, leave balances, time off,
  checklist and policies. Can request time off for themselves and acknowledge
  policies.
- **Manager.** Everything an employee can do, plus the profiles, time off,
  leave balances and checklists of their direct and indirect reports. Decides
  time off for those reports. Does not see their pay.
- **Admin (HR).** Sees and changes everything: records, pay, departments,
  accounts and roles, policies, reports and the audit log.

A role grants a capability. The reporting line sets its reach. A manager with
no reports can decide nothing, and an employee with reports gains nothing
until HR gives the account the manager role.

## Access rules

| Data or action | Employee | Manager | Admin (HR) |
| --- | --- | --- | --- |
| Directory and org chart: name, title, department, manager of current staff | Yes | Yes | Yes, plus people who left, status and salary |
| Profile page | Own | Own and reports | Everyone |
| Salary and compensation history | Own | Own | Everyone |
| Time off and leave balances: view | Own | Own and reports | Everyone |
| Time off: request | Self | Self | Anyone |
| Time off: approve or deny | No | Reports, never own | Anyone, never own |
| Onboarding and offboarding checklist | View own | View and complete for reports | View and complete for everyone |
| Policies: read and acknowledge for yourself | Yes | Yes | Yes |
| Policies: publish, see who has not acknowledged | No | No | Yes |
| Add, edit, delete employees; manage departments | No | No | Yes |
| Accounts, roles, password resets | No | No | Yes |
| Audit log | No | No | Yes |
| Directory CSV (includes salary) | No | No | Yes |
| Headcount and attrition report and its CSV | No | Own reporting tree | Whole company |

The directory and org chart are the company phone book, so every signed-in
person can see who works here and who they report to. Status, salary, people
who have left and everything on a profile page follow the stricter rows above.

"Reports" always means direct and indirect reports, found by walking
`manager_id` from the signed-in person's employee record. The walk is done on
the server for every request, so a change to the reporting line takes effect on
the next page load.

## Abuse cases

Each row is something an attacker or a curious colleague might try, and what
the server does about it.

| # | Abuse case | Control |
| --- | --- | --- |
| 1 | **IDOR on employee ids.** Change the id in `/employees/7`, `/employees/7/edit` or `/employees/7/delete` to read or change someone else. | Every handler loads the record and asks one policy module whether the signed-in account may see or change it. Out of scope gets the same 404 as a record that does not exist, so ids cannot be probed. |
| 2 | **IDOR on other ids.** Decide a time-off request, tick a checklist task or open an account by guessing its id. | Same pattern: the request, task or account is resolved to its employee, then checked against the caller's scope. |
| 3 | **CSRF.** A page on another site submits a form to PeopleBase with the victim's cookie. | Every POST needs the per-session token, compared in constant time. This includes sign-in and sign-out. Cookies are `SameSite=Lax` and `HttpOnly`. |
| 4 | **Session fixation and replay.** Plant a session before sign-in, or reuse a cookie captured before sign-out. | The session is cleared at sign-in and gets a new CSRF token. Each account carries a session nonce that is copied into the cookie and rotated at sign-out, password change, reset, role change and deactivation, so older cookies stop working. Sessions end 12 hours after sign-in. |
| 5 | **Privilege escalation through form fields.** Add `role=admin`, `employee_id=7`, `approver_id=1`, `status=approved` or `decided_by_id` to a form. | Forms are read field by field into validators; unknown fields are ignored. The acting person always comes from the session. Role changes are HR-only, nobody changes their own role, and the last active admin cannot be demoted or deactivated. |
| 6 | **Approving your own time off.** | Nobody decides their own request, including HR and managers. |
| 7 | **CSV export scope.** Fetch `/employees.csv` or the report CSV directly to get rows or columns the screen would not show. | The export runs the same scope filter as the page. The directory CSV carries salary, so it is HR-only. The report CSV is limited to the caller's reporting tree. Text cells are neutralized against spreadsheet formulas and responses are `no-store`. |
| 8 | **Password guessing.** Try many passwords for one account, or one password across many accounts. | Passwords are hashed with Argon2id. Five failures for one email lock sign-in for that email for 15 minutes, whether or not the account exists. Twenty failures from one address lock that address for 15 minutes. |
| 9 | **Account enumeration.** Learn which emails have accounts from error text, lockout behavior or response time. | One message for every failure. Lockout is keyed on the submitted email, not the account. An unknown email still costs one hash verification. |
| 10 | **Weak or shared passwords.** HR sets a password and keeps knowing it. | Passwords need 12 to 128 characters. A password set by HR or the CLI is temporary: the account must choose a new one at first sign-in before it can do anything else. |
| 11 | **Open redirect after sign-in.** Send a link with `next=https://evil.example`. | `next` is followed only when it is a local path. |
| 12 | **Reading pay data from a shared computer.** Press Back after sign-out, or read the browser cache. | Every page served to a signed-in account is `Cache-Control: no-store`. |
| 13 | **Tampering with the audit log.** Edit or delete an entry to hide a change. | The app has no route that changes or removes audit entries, and the model refuses updates and deletes at flush time. Entries are written in the same transaction as the change they describe. |
| 14 | **Access after leaving.** A former employee keeps using their account. | Setting a record to "left" deactivates the linked account in the same transaction, and every request rechecks that the account is active. |
| 15 | **Acting for someone else.** Acknowledge a policy or request leave in a colleague's name. | Acknowledgements and non-HR leave requests are always recorded against the signed-in person's own record. |
| 16 | **Script injection in stored text.** Put markup in a name, note or policy body. | Jinja autoescaping everywhere, policy text is shown as plain text, and the Content-Security-Policy forbids inline script. |

## What this does not cover

- No multi-factor authentication, single sign-on or emailed password reset. HR
  resets a password by setting a new temporary one.
- The session lives in a signed cookie. It cannot be forged without
  `SECRET_KEY`, but its contents (an account id and a nonce) are readable by
  whoever holds the cookie.
- The per-address sign-in limit uses the address Flask sees. Behind a reverse
  proxy that is the proxy unless the deployment sets up forwarded headers.
- The lockout can be used to keep a known email locked out. That is the usual
  trade for stopping guessing, and the lock lifts by itself after 15 minutes.
- Nothing is encrypted at the field level. Use an encrypted database volume
  and TLS at the host.
- The audit log is append-only inside the app. Someone with direct database
  access can still change it.
