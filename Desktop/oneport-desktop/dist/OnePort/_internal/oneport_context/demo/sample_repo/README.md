# TaskFlow

A small task-management API. Users create projects, add tasks, assign them to
teammates, and mark them done. It's a clean layered service: HTTP routes call
service objects, services enforce the business rules and persist through a thin
repository over SQLite.

This sample ships with `oneport-context` so you can see a narrated walkthrough
with no setup.
