---
title: Home
---

Temporary unstyled post list — real homepage/layout pending (Phase 5).

<ul>
{% for post in site.posts %}
  <li><a href="{{ post.url | relative_url }}">{{ post.title }}</a> — {{ post.date | date: "%-d. %B %Y" }}</li>
{% endfor %}
</ul>
