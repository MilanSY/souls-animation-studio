# Validation de l’extension installée

```sh
bash scripts/build_extension.sh
bash scripts/test_headless.sh
# Ou pour vérifier un ZIP précis :
bash scripts/test_headless.sh dist/souls_animation_studio-0.2.0.zip
```

Le test utilise un profil Blender temporaire hors ligne et le vrai ZIP installé,
non le code de l’extension dans le dépôt. Rigify doit être disponible dans la
distribution Blender testée (aucun téléchargement). `BLENDER_BIN` permet de
tester un autre exécutable. Un échec Python ou de `unittest` est remonté au shell.

La suite couvre le parcours par opérateurs (création du méta-rig, ajustement
automatique, retouche en Edit Mode, génération et skinning), le Quick Rig et
la déformation évaluée du bassin, de la colonne, de la tête, des mains et des
jambes/pieds. Le maillage synthétique est contrôlé comme une surface fermée
d’un seul composant et les joints du GLB doivent correspondre aux seuls os DEF.

## Import expérimental dans Godot

```sh
SOULS_TEST_GLB=/tmp/opencode/sas-reference.glb bash scripts/test_headless.sh
bash tests/test_godot_import.sh /tmp/opencode/sas-reference.glb
```

Le Blender headless conserve sur demande le GLB vérifié et sa référence
`<chemin>.json` (bornes dans le repère Godot, durée, joint et nombre de binds).
Le script Godot lit automatiquement ce JSON, ou accepte un JSON explicite en
deuxième argument. Sans JSON, il vérifie l’orientation et l’échelle
approximatives attendues du mannequin SAS. `GODOT_BIN` permet de choisir le
binaire. Le test importe via `GLTFDocument` dans un projet et un profil
temporaires, vérifie les bones/binds et applique l’animation à deux instants
pour observer le mouvement du bone de déformation.

Aucun de ces tests ne valide le rendu, les interactions de la sidebar, la
qualité visuelle de la déformation, ni l’intégration dans le projet du jeu.
