# One Fact, Two Chapters, Two Rulings

## Pattern

The model shows a session cookie set by a browser-facing application. The description names the cookie and says nothing about its attributes, its lifetime, or how the session behind it ends. Both the web-frontend and session-management chapters are in the selected level.

## Considered

Whether one ruling covers the cookie, or whether the same cookie earns a ruling in each chapter.

## Ruling

Accepted, in both — two rulings against two requirements, not one ruling spanning two chapters.

## Why

The standard partitions by question, not by artifact, and one artifact answers to more than one question. That is ordinary, and it is not duplication.

The frontend chapter asks what the *cookie* carries: `Secure`, `HttpOnly`, `SameSite`, a host prefix. Those are properties of the transport container, and the browser enforces them.

The session chapter asks what the *session* does: whether a fresh identifier is issued at login, whether it is invalidated on logout, whether an idle and an absolute timeout exist. Those are properties of the server's state, and the cookie is merely how it travels.

A single ruling would have to be filed under one chapter, which would leave the other chapter's coverage row saying the requirement was considered and nothing raised — untrue, and invisible to the reader.

The test for whether two rulings are really one: if the remedies differ, they are two. Adding `HttpOnly` does nothing for a session that never expires.

## What decided it

The cookie named in the description, read against each chapter's own subject. The same fact supported both rulings because each chapter asked a different question of it.
