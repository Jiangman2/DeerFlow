# Explicit group webhook delivery, Skill 2.1.7

The named November 16 report was generated and published, and its stored preview
retained a valid reader URL. It had no native Work private-chat binding. The
operator explicitly includes a group-bot webhook in the scheduled task text;
native private-chat delivery does not execute that separate instruction.

This release adds the Skill-owned `send_webhook.py` and a conditional reference.
After successful `present_files`, the agent copies the explicitly requested URL
once into private project configuration and runs the fixed sender. The sender
validates the accepted report, current host-owned conversation, reader identity,
and four matching files in the persistent thread outputs root. It sends one
Feishu-compatible rich-text hyperlink labeled `点击查看日报网页`, checks HTTP
and business success, and durably records target hashes and outcomes. It never
exports the webhook token. A pre-request exclusive journal prevents repeat
attempts for the same project/target/reader. Uncertain or interrupted sends are
not automatically retried. This is at-most-one attempt, not a distributed
exactly-once guarantee. Provider acceptance is not proof a person read a message.

The four published file copies are verified directly; the Skill does not ask the
model to transcribe a ToolMessage. The check is not independent host attestation.
The caller must first use the real `present_files` tool. Hosts need the standard
thread `workspace` and sibling `outputs` layout for this optional send branch.

The accepted five acquisition/calculation/render/browser sources remain byte
identical. Normal tasks without an explicit webhook keep their existing flow.
Native Work private-chat notification settings are unchanged and can operate as
a separate channel. This updater does not modify notification bindings or suppress
those platform-owned notifications. It does not modify Gateway, Nginx,
Provisioner, credentials or schedules, or replay reports/failed notifications.

The installer binds the accepted 2.1.6 receipt, stages a private backup, reuses
the existing public browser runtime, runs 57 isolated regressions and fixed PNG
plus dated-freight renders as UID1000 with the ordinary public-category mount,
and switches only the qualified Skill. Tests mock the sender; no real bot
request is sent by installation. Production rendering and future real webhook
delivery remain pending until the operator returns their receipts.

```bash
sudo python3 -I -B -u /home/cnooc/install-energy-webhook-skill-20261116-r2.py --install-webhook-skill-once
```

The r1 build was local-only and never supplied for production. Its unshipped
artifacts are retained; the finalized r2 fixes the fixture encoding before any
activation. Do not run historical installation commands or remove once markers.
If installation fails, retain its output without replaying it.

Payload and response semantics follow the official [custom-bot guide](https://open.feishu.cn/document/client-docs/bot-v3/add-custom-bot).
Only its Feishu-compatible v2 hook protocol is supported; protected bots may
reject a request under their existing keyword, signature or IP policy. Those
policies are not weakened or guessed by this release.
