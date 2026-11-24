# Souls Animation Studio — instructions du dépôt

- Reste dans ce dépôt. Ne modifie ni le jeu Godot ni la configuration globale OpenCode, Blender ou Omarchy. Aucun push sans demande.
- `addon-director` coordonne l’architecture et l’intégration. Il peut déléguer à `blender-developer` (extension et UI), `rigging-specialist` (armatures et export), `motion-ai-engineer` (animation et moteurs externes) ou `qa-tester` (tests). Les spécialistes ne redélèguent pas. Indique le périmètre de fichiers et les preuves attendues à chaque délégation.
- Pas de PyTorch, de poids ou de téléchargement au démarrage de Blender. Les moteurs externes ont chacun leur propre environnement, ne connaissent pas l’UI et ne reçoivent pas de chemin vers le projet Godot.
- Ne montre pas d’action non implémentée comme utilisable. Aucun moteur IA opérationnel au Sprint 0.
- Pour livrer : `bash scripts/build_extension.sh`, puis `bash scripts/test_headless.sh`. Communique les erreurs, l’environnement et les validations non effectuées.
- Ne redistribue pas de code tiers, poids ou données sans en avoir vérifié les droits et les notices requis. Préfère des fixtures synthétiques créées dans le dépôt.
