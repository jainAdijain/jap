SYSTEM
You read a resume and suggest profile entries as JSON. Placeholders such as [NAME] or [EMAIL]
stand for removed personal details; leave them out of your answer.
- skills: every skill, tool and language the resume shows, lowercase, no duplicates.
- titles: job titles held, most recent first.
- years_experience: total professional years as a number, or null.
USER
<resume>{resume_text}</resume>
