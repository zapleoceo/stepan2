"""Public legal pages: privacy policy, terms, data deletion.

Meta App Review will not look at an app whose privacy policy, terms and data-deletion route
are missing or unreachable, so these are static, self-contained HTML with no auth and no DB —
a reviewer or crawler can always fetch them.

Written to describe what this system ACTUALLY does, in both roles it has: it is the operator
of this website (the demo chat and the pixel on the landing) and a processor acting for client
businesses whose channels it answers. A policy that only covered the second role would be
silently wrong about the visitor reading it.
"""
from __future__ import annotations

_CONTACT = "privacy@zapleo.com"
_COMPANY = "ФОП Запорожець Дмитро Олегович (Zapleo Soft)"
_ADDRESS = "vul. Tonelna 56, Dnipro, 49000, Ukraine"
_APP = "Stepan"
_UPDATED = "28 July 2026"

_CSS = (
    "max-width:760px;margin:0 auto;padding:2.5rem 1.25rem;line-height:1.6;"
    "font-family:system-ui,-apple-system,Segoe UI,Roboto,sans-serif;color:#1a1a1a"
)
_NAV = (
    '<p style="font-size:.85rem;color:#666">'
    '<a href="/">Home</a> · <a href="/privacy">Privacy</a> · <a href="/terms">Terms</a> · '
    '<a href="/data-deletion">Data deletion</a></p>'
)


def _page(title: str, body: str) -> str:
    return (f'<article style="{_CSS}"><h1>{title}</h1>'
            f'<p><em>Last updated: {_UPDATED}</em></p>{body}'
            f'<hr style="margin:2rem 0;border:0;border-top:1px solid #e5e5e5">{_NAV}</article>')


_PRIVACY = _page(
    f"Privacy Policy — {_APP}",
    f"""
<p>{_APP} is an AI sales agent operated by {_COMPANY}, {_ADDRESS} (“we”, “us”). We act in two
different roles, and this policy covers both.</p>

<h2>1. When you use this website</h2>
<p>Here we are the controller of your data.</p>
<ul>
  <li><strong>The demo chat.</strong> What you type is sent to an AI model provider to generate
      the reply. The conversation is not stored in a database — it lives only in your browser
      tab and is gone when you close it.</li>
  <li><strong>Contact details you volunteer.</strong> If you give an email, phone or messenger
      handle because you want to be contacted, that message is forwarded to our team so we can
      reply. We use it only to answer you.</li>
  <li><strong>Meta pixel.</strong> When advertising is running, this site loads the Meta pixel,
      which reports page views and whether you opened or wrote in the chat. It is subject to
      Meta's own data policy. Blocking third-party scripts stops it and the site still works.</li>
</ul>

<h2>2. When you write to a business that uses {_APP}</h2>
<p>Here the business is the controller and we are its processor: we act on its instructions
and do not use your data for our own purposes.</p>
<ul>
  <li><strong>Your messages</strong> on Instagram Direct, Messenger or WhatsApp, and the public
      profile fields the platform provides (name, username, profile picture, platform user id).</li>
  <li><strong>Conversation metadata</strong>: timestamps, which account you wrote to, and the
      advertisement the conversation started from, when the platform reports it.</li>
  <li><strong>Contact details you provide in the chat</strong>, such as a phone number, when you
      ask to be contacted or to be enrolled.</li>
</ul>
<p>We process this only after <strong>you start the conversation</strong>. We do not send
unsolicited first messages.</p>

<h2>3. Why we process it</h2>
<ul>
  <li>To read your message and answer your question.</li>
  <li>To follow up inside the platform's messaging window when you asked us to.</li>
  <li>To hand the conversation to a human when you need one.</li>
</ul>
<p>We do <strong>not</strong> sell your data and do not use conversations to target advertising.</p>

<h2>4. Who else sees it</h2>
<p>Data is stored on our own servers in the European Union. It is shared only with providers
strictly needed to run the service — hosting, and the AI model providers that generate replies —
under confidentiality terms. The client business whose account you wrote to sees your
conversation. We may disclose data where the law requires it.</p>

<h2>5. How long we keep it</h2>
<p>Conversations are kept while the client business needs them to serve you, and are deleted on
request (see <a href="/data-deletion">Data deletion</a>). Website demo chats are not stored at
all; a contact you volunteered is kept until we have answered you and you ask us to remove it.</p>

<h2>6. Your rights</h2>
<p>You may ask for access to your data, correction of it, or its deletion, and you may object to
processing. Write to <a href="mailto:{_CONTACT}">{_CONTACT}</a>. You can also block the account
you wrote to at any time and the messages stop immediately.</p>

<h2>7. Contact</h2>
<p>{_COMPANY}<br>{_ADDRESS}<br><a href="mailto:{_CONTACT}">{_CONTACT}</a></p>
""")

_TERMS = _page(
    f"Terms of Service — {_APP}",
    f"""
<p>These terms govern the use of {_APP} (“the Service”), operated by {_COMPANY}, {_ADDRESS}.
By using the Service you agree to them.</p>

<h2>1. What the Service does</h2>
<p>{_APP} answers messages that reach a business's Instagram, Messenger or WhatsApp accounts,
qualifies the people writing, and hands the ones ready to buy to that business's team. Replies
are generated by AI models from the facts the business supplies.</p>

<h2>2. Who may use it</h2>
<p>The Service is for businesses and their staff. You must be at least 18 and authorised to act
for the business whose accounts you connect. You are responsible for keeping your access
credentials safe.</p>

<h2>3. Your obligations</h2>
<ul>
  <li>Connect only accounts you are entitled to operate.</li>
  <li>Supply accurate facts — prices, terms, availability — for the agent to rely on.</li>
  <li>Do not use the Service to send unsolicited bulk messages, to deceive, or to break the
      rules of Meta, WhatsApp or any other platform you connect.</li>
  <li>Comply with data-protection law towards the people who write to you; where we process
      their data for you, we do it on your instructions.</li>
</ul>

<h2>4. AI-generated replies</h2>
<p>Replies are produced automatically. The agent is constrained to the facts you supply and is
built to refuse to invent prices or promises, but automated output can still be wrong. You are
responsible for what your accounts say to your customers, and we recommend reviewing the
conversation log. Nothing the agent says forms a binding offer unless you confirm it.</p>

<h2>5. Fees</h2>
<p>Pricing is what we agree with you in writing before the Service starts, including any free
allowance. Charges are for leads handled, not per message.</p>

<h2>6. Availability</h2>
<p>We work to keep the Service running but do not guarantee uninterrupted operation: the
messaging platforms we depend on can change, rate-limit or suspend access outside our control.
We may change or discontinue features, and will give reasonable notice of material changes.</p>

<h2>7. Liability</h2>
<p>To the extent permitted by law, we are not liable for indirect or consequential loss,
including lost profit or lost business opportunity. Nothing here excludes liability that cannot
be excluded by law.</p>

<h2>8. Termination</h2>
<p>You may stop using the Service and disconnect your accounts at any time. We may suspend
access if these terms are broken or if a platform requires it. On termination you can request
export or deletion of your data.</p>

<h2>9. Governing law and contact</h2>
<p>These terms are governed by the law of Ukraine. Questions:
<a href="mailto:{_CONTACT}">{_CONTACT}</a>.</p>
""")

_DELETION = _page(
    "Data deletion",
    f"""
<p>You can have your data deleted. This page is the route Meta requires us to publish, and it
works whether or not you came from Meta.</p>

<h2>How to request deletion</h2>
<p>Email <a href="mailto:{_CONTACT}">{_CONTACT}</a> from the address you used, or with the
username or phone number you messaged us from, and write “delete my data”. Tell us which
business's account you wrote to if you remember it — it makes the record easier to find.</p>

<h2>What we delete</h2>
<p>The conversation, the contact details taken from it, and the profile fields the platform gave
us for you. We complete this within <strong>30 days</strong> and confirm by reply.</p>

<h2>What we may keep</h2>
<p>Only what the law obliges us to keep — for example accounting records where a purchase
happened. Anything kept is isolated and not used to contact you.</p>

<h2>Faster ways to stop messages</h2>
<p>Blocking or reporting the account you wrote to on Instagram, Messenger or WhatsApp stops all
messages immediately, without waiting for us. Deleting the conversation on your side removes it
from your view but does not by itself delete our copy — use the email above for that.</p>

<h2>The website demo chat</h2>
<p>Nothing to delete: the demo chat on this site is not stored in a database. If you volunteered
a contact and want it removed, email us and it is gone.</p>
""")


def privacy_html() -> str:
    return _PRIVACY


def terms_html() -> str:
    return _TERMS


def data_deletion_html() -> str:
    return _DELETION
