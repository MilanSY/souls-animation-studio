# OpenCode V1 — configuration du dépôt

Le dépôt cible OpenCode V1 (version de référence : **1.18.34**) sous Linux et Windows. `.opencode/opencode.jsonc` sélectionne `addon-director` comme agent principal par défaut. Les quatre spécialistes déclarés dans `.opencode/agents/` (`blender-developer`, `rigging-specialist`, `motion-ai-engineer` et `qa-tester`) utilisent `mode: subagent` et sont appelés par l’outil `task`. Aucun modèle, chemin absolu ni programme propre à un OS n’est imposé par le dépôt.

Le fichier de projet et les cinq profils utilisent la clé V1 `permission` au singulier, avec la règle générique `"*": "allow"`. Cette règle ne demande pas de confirmation au niveau du dépôt et peut aussi élargir les droits des agents intégrés. Elle n’installe aucun serveur MCP et ne lève pas les restrictions du runtime. La redélégation dépend de la disponibilité de `task` dans la session du spécialiste, pas d’un réglage de profondeur dans ce dépôt.

**Ce n’est pas un bac à sable.** Les agents doivent suivre [`AGENTS.md`](../AGENTS.md), notamment travailler dans le dépôt ou dans des répertoires de test isolés, ne modifier ni le système ni la configuration globale, et ne pas pousser sans demande explicite.

Depuis la racine du dépôt, les commandes suivantes vérifient la version, la configuration résolue et les profils :

```text
opencode --version
opencode debug config
opencode agent list
opencode debug agent addon-director
```

Pour tester la délégation, ouvre une nouvelle session avec `addon-director` et demande-lui d’appeler chaque spécialiste via `task` pour une vérification en lecture seule. Vérifie les quatre appels et leurs retours, pas seulement le résumé du directeur. Quitte et redémarre OpenCode après toute modification de la configuration ou des profils.
