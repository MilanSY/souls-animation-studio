# Validation de l’extension installée

```sh
bash scripts/build_extension.sh
bash scripts/test_headless.sh
# Ou pour vérifier un ZIP précis :
bash scripts/test_headless.sh dist/souls_animation_studio-0.5.0-dev-sprint-5a.zip
bash tests/test_shell_failures.sh dist/souls_animation_studio-0.5.0-dev-sprint-5a.zip
```

Les tests de parcours utilisent un profil Blender temporaire hors ligne et le
vrai ZIP installé. Certaines suites unitaires testent également les modules
directement depuis le dépôt, comme les tests CPython. Rigify doit être disponible dans la
distribution Blender testée (aucun téléchargement). `BLENDER_BIN` permet de
tester un autre exécutable. Un échec Python ou de `unittest` est remonté au shell.
La sonde d’échec vérifie ce comportement avec un ZIP corrompu et une assertion
Python volontairement fausse dans un vrai processus Blender. Le lanceur vérifie
aussi la désactivation/réactivation, puis la désinstallation réelle et l’absence
de fichiers et d’enregistrements de l’extension après redémarrage.

La suite couvre le parcours par opérateurs (création du méta-rig, ajustement
automatique, retouche en Edit Mode, génération et skinning), le Quick Rig et
la déformation évaluée du bassin, de la colonne, de la tête, des mains et des
jambes/pieds. Le maillage synthétique est contrôlé comme une surface fermée
d’un seul composant et les joints du GLB doivent correspondre aux seuls os DEF.

Les tests Smart Pose vérifient les commandes de sélection et de remise à zéro
partielle, le verrouillage des pieds avec ou sans cible, la prise à deux mains
avec un accessoire synthétique, les résidus de contact aux images clés et aux
images générées, et la conservation de l’Action source. Ils comparent les os DEF
évalués et journalisent les écarts et le temps de calcul (pas la fluidité du
viewport). La bibliothèque est testée pour la capture, l’application partielle,
le mélange, le miroir, le duplicata, la suppression, l’insertion de clés et le
JSON. Un second Blender rouvre un fichier `.blend` pour revérifier une pose
enregistrée et un contact borné, sans auto-exécution de scripts. Le test
Undo/Redo initialise explicitement la pile headless et appelle les opérateurs
en mode `EXEC_DEFAULT` : il ne teste pas les gestes clavier ou souris.

## Import expérimental dans Godot

```sh
SOULS_TEST_GODOT=1 bash scripts/test_headless.sh
# Ou uniquement le GLB personnage de référence :
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
qualité visuelle de la déformation, la collision du pied avec une surface, ni
l’intégration dans le projet du jeu. Le ghost n’est pas implémenté ; le test
vérifie qu’il est signalé comme indisponible, sans bouton utilisable.

## AI Inbetweening expérimental

Le run standard vérifie l’adaptateur, les erreurs, l’annulation et le retargeting sur fixtures, sans exiger NumPy. Si NumPy est présent, le test de processus exerce aussi le vrai worker avec des poids synthétiques de diagnostic. Ce test ne remplace pas celui du checkpoint entraîné. Pour rendre obligatoire la preuve neuronale avec le checkpoint de démo et l’aller-retour GLB :

```sh
SAS_MOTION_TEST_PYTHON=/chemin/absolu/venv/bin/python SOULS_TEST_GODOT=1 bash scripts/test_headless.sh
```

La preuve compare à la requête issue de la démo, puis refait le bake avec l’adaptateur installé. Les événements modaux sont injectés en headless : pas de recette de la fenêtre Blender.

Les tests du [banc humanoïde et de la correction des raccords](../docs/sprint-5a.md) utilisent des fixtures originales déterministes, sans modèle ni poids. Dans le ZIP, deux tests qualifient le retargeting FK sur les morphologies Sprint 3 et quatre tests vérifient les contacts natifs sur une fixture BVH, dont le rejet d’une copie qui masque un contact. Aucun résultat de ces tests ne prouve une inférence humanoïde. Un ZIP historique ne possède pas ces composants : rejouer sa suite à la révision correspondante.
