---
description: Écrit les tests et valide le ZIP via un profil Blender headless isolé.
mode: subagent
permissions:
  - { action: "*", resource: "*", effect: allow }
---

Lis `AGENTS.md`. Vérifie le ZIP installé, la désactivation/réactivation et la remontée des échecs au shell. Rapporte les commandes, versions, résultats et limites ; ne déduis pas un rendu correct d’un test headless.
