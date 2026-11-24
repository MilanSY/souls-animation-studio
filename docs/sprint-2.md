# Rapport — Sprint 2, version 0.3.0

## Audit avant développement

Base : `2e3f83d`, version Blender du Sprint 1 `0.2.0`. Relus : rapport Sprint 1, architecture, guide utilisateur, tests et pipeline de démo. Plateforme : Omarchy / Arch Linux, Intel HD Graphics 520, Blender 5.2.2 LTS (`d13f752e3b9c`), Python 3.14.8, Rigify 0.6.10 et Godot 4.7.2 stable. Tests sur profils et projets temporaires, hors ligne.

Les 10 tests du ZIP Sprint 1, 4 tests CPU, 4 tests Blender/Rigify et les deux imports Godot ont réussi. La comparaison Blender/GLB a reproduit l’écart maximal d’environ 10 mm en mouvement. Aucun blocage ou régression fonctionnelle du Sprint 1 n’a été détecté au départ. MeshOptimizer demeure absent du paquet Blender : l’export non compressé reste fonctionnel. Les limites de recette interactive et de peau du Sprint 1 ne sont pas des résultats validés du Sprint 2.

## Priorités et fonctionnement

- **P0 — Smart Pose Editor.** Sélection des widgets natifs Rigify en vue 3D ou par boutons ; détection du contexte IK/FK pour les extrémités et les directions de coude/genou. Les transformations sont celles de Blender. Undo/Redo a passé un test natif en headless, avec pile initialisée, pour la capture de pose et le verrouillage de contact. Aucun parcours souris/viewport complet n’a été exécuté.
- **P0 — Contacts, accessoire et bibliothèque.** Ancrages indépendants pour chaque membre, position et orientation optionnelle, activation par plage, mesures sur les os DEF. L’accessoire suit la main principale, la seconde main suit l’accessoire. Le `.blend` contient les poses et les attaches ; le JSON échange les huit poses originales. Pas de données tierces ou de poids embarqués.
- **P0 — CPU Inbetweening avec contacts.** SLERP pour rotations prises en charge, copie d’action et vérification exacte des matrices évaluées aux images clés. Le calcul de mouvement reste séparé de la résolution native de contacts. Le rapport distingue les résidus des poses générées et ceux des clés immuables. Pas de résolution globale des conflits, pas de prédiction IA.
- **Reportés.** Ghost Poses et équilibre statique. Aucune simulation physique ; **AI Inbetweening** est explicitement indisponible. L’overlay Ghost n’est pas livré : aucun nettoyage de données Ghost ni coût de rendu Ghost à mesurer.

## Démo et mesures

[`sas-combat.blend`](../demo/sas-combat.blend) : mannequin continu du Sprint 1, épée d’entraînement synthétique, trois poses clés (1/13/25) et clip CPU Smooth. Le rig comporte deux contacts pieds et une prise secondaire ; la main droite pilote l’objet. Les limites d’orientation et de distance ont été respectées pour **75 mesures** sur les 25 images. Écart de contact de position maximal **~0,063 mm** en espace monde pour ce scénario. Une cible volontairement inaccessible ou invalide est signalée dans les tests ; le solveur ne force pas le déplacement d’une clé.

Temps par lancement observé : **~3,1 à 4,1 s** pour ouvrir le mannequin déjà riggé, construire le clip, l’exporter et sauver le `.blend` en headless. L’étape glTF a pris **~0,57 à 1,01 s**. La sonde GLB mesure sur cinq images un écart maximal **9,25 mm**, moyenne par image **≤1,46 mm**, par rapport à la surface évaluée dans Blender. Ce sont des mesures sur un seul personnage et non un benchmark de viewport.

Sur le `.blend` rouvert, cinq exécutions du clip 1–25 ont donné des médianes de **19 ms** pour l’interpolation seule et **243 ms** pour interpolation, évaluation des contacts et vérification des ancres (plages observées : 18–32 ms et 239–262 ms).

Le GLB du personnage s’importe via Godot 4.7.2 avec **35 DEF et 35 binds** et un clip d’**1 s**. L’import éditeur headless a aussi produit une `PackedScene` chargeable dans un projet jetable ; la lecture fait tourner l’os DEF du bras gauche d’environ 0,081 rad entre 0 et 0,5 s. L’épée n’est pas incluse dans l’export expérimental monoskin ; elle est visible et animée dans Blender. Les deux premières variantes de pose de la démo ont révélé un cisaillement relatif du poignet gauche au-delà du seuil glTF. Les poses livrées ont une prise ajustée et passent le seuil existant ; celui-ci n’a pas été relâché.

[Revue de rendus fixes Cycles CPU](../demo/combat-visual-review.jpg) : de face puis de trois-quarts, images 1/7/13/19/25. L’accessoire et la prise restent visuellement liés sur ces images. Pincements au pli des épaules/coudes et mains non articulées : cette revue ne valide ni une prise digitale ni l’absence d’intersection, le réalisme physique, ou la fluidité GPU. La vérification visuelle en Godot et un parcours complet de viewport sur la HD 520 restent à réaliser.

## Tests de livraison

- **18 tests du ZIP installé**, dont 10 conservés et 8 SmartPose : sélection, transformations et reset, Undo/Redo, pieds, prise secondaire, poses et miroir, résidus et copie d’action, sauvegarde/réouverture. Installation, désactivation, réactivation et désinstallation réelle contrôlées dans un profil jetable. Le lanceur complet a pris ~55 s pendant la QA.
- Une sonde supplémentaire de prise à **une main** sur la démo rouverte déplace l’épée de **41,83 mm** avec la main principale, sans créer de contact secondaire. Un essai graphique automatisé par le spécialiste Blender a exercé le dessin natif des panneaux et le retour Undo après une translation ; ce n’est pas une recette souris ni une mesure de fluidité.
- **4 tests CPU et 6 tests Blender/Rigify** hors ZIP. Le test de portée impossible rapporte notamment une cible de main à ~19,90 unités du résultat évalué, aux images générées et aux clés.
- Export QA et deux artefacts de démo importés dans Godot. Les tests négatifs remontent un code shell non nul pour un ZIP corrompu et une assertion Python injectée. Le nettoyage Blender signale le dépôt distant `blender_org` absent du profil vierge, sans empêcher la désinstallation.
- **Validation indépendante finale** : construction et 18/18 tests du ZIP en 51,4 s, 10/10 tests backend, trois imports Godot et comparaison de surface réussis. Les 25 entrées du ZIP correspondent aux sources et à `LICENSE`. Les démos ne contiennent pas de texte exécutable Rigify ni de lien vers une bibliothèque. SHA-256 du ZIP validé : `d8fccd293a87e2d2a082e57217a475b5d8557d747f6ac79f4e50615b67ab279e`.

## Reproduire

```sh
bash scripts/build_extension.sh
bash scripts/test_headless.sh
bash tests/test_godot_import.sh demo/sas-combat.glb
python3 backends/inbetweening/test_cpu.py
profile=$(mktemp -d)
export BLENDER_USER_RESOURCES="$profile"
blender -b --factory-startup --offline-mode --disable-autoexec --python-exit-code 1 \
  --python backends/inbetweening/test_blender.py
blender -b --factory-startup --offline-mode --disable-autoexec demo/sas-humanoid.blend \
  --python-exit-code 1 --python scripts/build_combat_demo.py -- /tmp/opencode/sas-combat
blender -b --factory-startup --offline-mode --disable-autoexec demo/sas-combat.blend \
  --python-exit-code 1 --python scripts/compare_demo.py
rm -rf -- "$profile"
```

## Recommandations Sprint 3

1. Étendre la recette Blender interactive : Undo/Redo réel, ergonomie des widgets et des états de plage, réactivité sur la HD 520, poses avec mains articulées.
2. Renforcer la résolution de contacts : plusieurs intervalles par membre, transitions continues, détection des collisions et compensation bornée du bassin entre clés. Garder des tests sur les os DEF, pas uniquement sur les widgets.
3. Formaliser l’export et le retarget des accessoires dans un projet Godot isolé, avec essais de plusieurs morphologies et contrôle par image.
4. Prototyper des Ghost Poses par cache de segments (sans dupliquer la peau), avec tests de nettoyage et mesures sur la HD 520. Évaluer ensuite un prototype d’équilibre statique puis le contrat d’un moteur externe CPU ; ne présenter aucune approximation ou interpolation comme une simulation ou un modèle IA.
