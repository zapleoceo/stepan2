"""Public privacy policy page — required by Meta App Review for a Messenger/Instagram app.

Static, self-contained HTML (no auth, no DB) so Meta's reviewers and crawler can always fetch
it. Kept deliberately plain and factual; update the contact email / company name below to match
the verified business before submitting App Review.
"""
from __future__ import annotations

_CONTACT_EMAIL = "privacy@zapleo.com"
_COMPANY = "T4Y ITStep 2R"
_APP = "Stepan"

_PRIVACY_HTML = f"""
<article style="max-width:760px;margin:0 auto;padding:2.5rem 1.25rem;line-height:1.6;
 font-family:system-ui,-apple-system,Segoe UI,Roboto,sans-serif;color:#1a1a1a">
<h1>Privacy Policy — {_APP}</h1>
<p><em>Last updated: 11 July 2026</em></p>

<p>{_APP} is a customer-messaging assistant operated by {_COMPANY} (“we”, “us”). It replies to
people who contact our business Pages on Facebook Messenger and Instagram Direct. This policy
explains what data we process and why.</p>

<h2>1. What we process</h2>
<ul>
  <li><strong>Messages you send us</strong> on Messenger / Instagram Direct (text and, where
      applicable, attachments), and your public profile fields provided by Meta (name,
      profile picture, and the platform-scoped user id).</li>
  <li><strong>Conversation metadata</strong>: timestamps, the Page/account you wrote to, and
      the advertisement a conversation started from (when Meta provides it).</li>
</ul>
<p>We process this data only after <strong>you initiate a conversation</strong> with our Page
or account. We do not send unsolicited messages.</p>

<h2>2. Why we process it</h2>
<ul>
  <li>To read your message and reply to your question about our courses/services.</li>
  <li>To follow up within the platform's standard messaging window if you asked us to.</li>
  <li>To let a human manager take over the conversation when needed.</li>
</ul>
<p>We do <strong>not</strong> sell your data, and we do not use it for advertising targeting.</p>

<h2>3. How long we keep it</h2>
<p>Conversation data is retained only as long as needed to serve you and to keep a record of
our correspondence. You can ask us to delete your conversation data at any time (see below);
we also delete it when you delete the conversation on your side and it is no longer needed.</p>

<h2>4. Sharing</h2>
<p>Data is stored on our own infrastructure. We share it only with service providers strictly
necessary to operate the assistant (hosting, and the AI model provider used to generate a
reply), under confidentiality obligations. We may disclose data if required by law.</p>

<h2>5. Your rights</h2>
<p>You can request access to, correction of, or deletion of your data. To do so — or to
request that we stop messaging you — contact us at
<a href="mailto:{_CONTACT_EMAIL}">{_CONTACT_EMAIL}</a>. You can also block the Page/account on
Meta at any time to stop all messages.</p>

<h2>6. Data deletion</h2>
<p>To delete your data, email <a href="mailto:{_CONTACT_EMAIL}">{_CONTACT_EMAIL}</a> with the
account you used to message us; we will delete the associated conversation data within 30 days
and confirm by reply.</p>

<h2>7. Contact</h2>
<p>{_COMPANY} — <a href="mailto:{_CONTACT_EMAIL}">{_CONTACT_EMAIL}</a></p>
</article>
"""


def privacy_html() -> str:
    return _PRIVACY_HTML
