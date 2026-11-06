# Company Brain: user guide

Company Brain answers questions about your client documents, summarises them and drafts proposals. Everything runs on this Mac and the firewall blocks it from the internet. For now it holds only fictional and public test documents.

All the buttons are files in the **launchers** folder of the project. Double-click one and a small window opens and tells you what is happening.

## Start and stop

- **Start AI** checks the firewall, then starts the assistant and opens the chat window in your browser (http://127.0.0.1:8080). It takes about a minute. It also updates the documents in the background. If the firewall check fails, it does not start: read the message and ask for help. Never switch the firewall off to get round it.
- **Stop AI** stops everything and frees the Mac's memory. If a document update or a backup is running, it waits for it to reach a safe point first.
- **Check health** tells you, in plain words, whether each part is running, whether the firewall is in place, when the documents were last updated and when the last backup was made.

Answers are faster with Chrome and other large apps closed. While an answer is being written, the Mac may be slower to switch apps. This is normal.

## Ask, summarise and draft

In the chat window, pick a mode at the top, then start a new chat for each client.

1. The assistant asks "Which client?". Reply with the number or the name. That chat stays about that client only.
2. Type your question or request and press Return.

The modes:

- **Company Brain** answers a question. Each fact carries a source number, such as [1]. Click a source under the answer to see the passage it came from. If the documents do not say, it tells you so instead of guessing.
- **Company Brain: summarise a document** summarises one document, for example "Summarise the call of 27 August 2026". Under the summary there are **Word** and **PDF** download links.
- **Company Brain: draft a proposal** drafts a proposal that follows your methodology. It covers background, objectives, measures of success, value, three options with fixed fees paid in monthly instalments in advance, and the optional retainer. Fees and terms are shown as suggestions, not as agreed. Anything still to be decided appears under "Questions for you". It also has **Word** and **PDF** downloads.

Always check a draft before you send it. Text in the box "Suggestions, not agreed" is the assistant's proposal, not a fact from your documents.

## Keep the documents up to date

- The documents are updated when you start the assistant, every night at 01:30 while you are logged in, and whenever you double-click **Update documents**.
- Check health shows the date and what changed.

## Backups

- **Set up backups**: run it once. It shows your recovery key one time only. Copy it into your password manager, or write it on paper, before you close the window. Never paste it into a chat. Without it, no backup can be opened. Backups are also signed, so a backup made by anyone else is never restored. If you set up backups before 25 October, run it once more: it asks for your recovery key one time, in a hidden prompt.
- After that, a backup is made every night at 02:00 into "Company Brain Backups" in Google Drive. It is encrypted, and only your recovery key can open it. Backups are kept for 30 days. Check health shows the last one.
- **Restore backup** puts back the newest backup. It asks you to type yes, then to paste your recovery key into a hidden prompt. If anything goes wrong, your current data stays in use.

## Practise a restore (supervised)

Do this once with your helper present, so restoring is familiar before you ever need it.

1. Double-click **Check health** and check that it shows a recent backup.
2. Have your recovery key ready from your password manager or paper.
3. Double-click **Stop AI**, then **Restore backup**.
4. Check the backup date it names, then type yes and press Return.
5. Paste your recovery key when asked. It stays hidden. Press Return.
6. Wait for "The backup was restored and checked."
7. Double-click **Start AI** and ask a question you know the answer to.

## Other buttons

- **Update software** updates the assistant's programs and the chat window. It stops the AI, takes about 30 minutes and checks everything. If anything fails, it puts the previous versions back. Use it when your helper suggests it, not before a meeting.
- **Show test results** opens the page with the latest test results. It works offline and contains no links.
- **Rate answers** opens a page with 20 of the assistant's answers. Give each a score from 1 to 5 (the page explains the scores), then click **Save my scores**: the button stays grey until every answer has a score. Go back to the small window and press Return; it saves your scores. Nothing is sent anywhere.
- Deleting a client: ask your helper to run the delete-a-client routine. It removes the client from the assistant, then you move or delete the client's folder in Google Drive. Backups keep the client for up to 30 days.

## When something goes wrong

- "The assistant is not running": double-click **Start AI**, wait a minute, and ask again.
- No answer, or it is very slow: close Chrome and other large apps, and try a shorter question.
- Start AI says the firewall is not set up: do not change the firewall yourself. Ask for help.
- Check health shows "Problem": read the line. A failed backup or update is retried automatically. If the same problem appears two days in a row, ask for help.
- You can always double-click **Stop AI**, then **Start AI**.
