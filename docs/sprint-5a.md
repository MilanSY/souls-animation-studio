# Sprint 5A — fondations de l’inbetweening humanoïde

Branche de développement `sprint-5a-portable`, base `af26e46`. Le build local `dist/souls_animation_studio-0.5.0-dev-sprint-5a.zip` garde la version manifeste **0.5.0** et ne remplace pas l’archive publique. Il ne propose aucune génération humanoïde dans la sidebar. **Aucune inférence Kimodo** : ne pas confondre les fixtures ou la correction des raccords avec une génération neuronale.

## Audit de départ

- Avant création de la branche, le dépôt était propre et GitHub confirmait `master` à `af26e4673bfa4c9fefc7edb3dce73776ebca9e06`. Aucune branche distante `sprint-5a-portable` n’était annoncée.
- Omarchy 4.5.0 / Arch Linux, noyau `7.2.15-4-omarchy`, Intel Core i5-6300U, Intel HD Graphics 520, 15 Gio visibles (4,8 Gio disponibles au relevé). Blender 5.2.2 LTS, Rigify 0.6.10, CPython 3.14.8 (système et Blender), NumPy système déjà présent 2.5.4, Godot 4.7.2, Git 2.56.0, OpenCode installé 2.4.1. PyTorch non disponible dans le Python système ; `rocminfo`, `hipcc` et `nvidia-smi` non trouvés dans le `PATH`. OpenCode **1.18.34** a chargé en environnement temporaire la configuration Linux inchangée : `addon-director` et les quatre profils spécialistes listés. Pas d’essai Windows ni de délégation réexécutée sous V1 (configuration inchangée).
- Recette sur une extraction isolée de `af26e46` avec `SAS_MOTION_TEST_PYTHON=/usr/bin/python3` et `SOULS_TEST_GODOT=1` : **76 réussites, 7 skips de répartition CPython/Blender, 0 échec**, hors vérifications supplémentaires du retarget planaire, du MLP entraîné et des trois imports Godot, qui passent aussi. Une première tentative sur l’arbre modifié simultanément par les agents n’est pas une référence : un fichier de transport était temporairement absent. L’extraction a supprimé cette interférence.
- Le CPU, les ancres/slots, les contacts natifs et le bake MLP existaient déjà. Ils restent les chemins de référence ; les nouvelles briques ne remplacent pas les opérateurs historiques. Voir les rapports [Sprint 3](sprint-3.md), [Sprint 4](sprint-4.md) et [mesure de continuité planaire](sprint-4-continuite.md).

## Recette en fenêtre — à effectuer (non exécutée)

1. Construire le ZIP, puis **Édition → Préférences → Obtenir des extensions → Installer depuis le disque** dans un profil de test. Choisir le fichier `-dev-sprint-5a.zip`, sans écraser une installation de production ; vérifier Rigify local et ouvrir `N → Souls Animation Studio`. Travailler dans un `.blend` jetable, hors du jeu.
2. Importer un **nouveau** personnage symétrique en T/A, `+Z` haut et `−Y` avant. Dans **Quick Rigging**, utiliser **Create Meta-rig → Fit to Mesh → Adjust in Edit Mode** : déplacer volontairement une extrémité d’os (`G`), contrôler sa position de face et de profil, confirmer la retouche. **Generate Rig → Bind (GLB workflow) → Validate Rig**. En mode Pose, vérifier les déformations des épaules et des hanches avec les widgets et tester le passage IK/FK. Voir le [guide détaillé](guide-utilisateur.md).
3. Créer une Action source et trois poses de combat (garde, attaque, retour ou esquive) aux images 1, 13 et 25. Insérer les clés des contrôleurs utilisés avec `I` ou **Insert Keyframe** ; vérifier les clés et le slot dans l’Action Editor. Avec les jambes en IK, épingler les pieds via **Contacts → Lock Contact**, d’abord sur une plage courte ; se déplacer hors de la plage pour vérifier la libération.
4. Placer une arme de test aux mains. En IK, **Weapon Setup**, choisir l’arme, la main primaire et le mode deux mains ; déplacer la main primaire et vérifier le suivi de l’arme et de l’autre main. Ne pas parenter l’arme une seconde fois. Consulter les [règles de contact](pose-editor.md).
5. Dans **CPU Inbetweening**, essayer **Linear** puis **Smooth** et la génération avec contacts sur 1–25. Sélectionner la copie dans l’Action Editor, lire et scruter la timeline (y compris avant et après les plages), retoucher un contrôleur généré et rebasculer sur la source. Relever les résidus `SAS Contact Report`, particulièrement aux clés ; ne pas qualifier visuellement un conflit comme résolu.
6. Sur le rig de la [démo planaire](sprint-4.md#utiliser-le-modèle), pas sur l’humanoïde, ouvrir le panneau MLP. Fournir Python/NumPy, worker et poids existants, vérifier les indications de rig/cadence et la case de confiance, puis essayer un lancement, l’annulation et l’assignation manuelle de l’Action de résultat. Les tests automatisés injectent les événements modaux : ils ne valident ni les dialogues ni la fluidité en fenêtre.

Aucun changement d’interface n’a été nécessaire pour cette fondation. Le panneau historique nomme déjà le MLP, signale le domaine planaire, exige des fichiers locaux de confiance et annonce que le résultat n’est pas assigné. Les briques humanoïdes n’y sont pas affichées comme utilisables.

## Rigify et contacts : deux qualifications distinctes

- L’échange humanoïde lit les **DEF évalués** selon un mapping anatomique explicite. Il sérialise des poses et un repos parents-locaux, en mètres/WXYZ, dans le repère canonique de l’armature (sa position et sa rotation dans la scène ne constituent pas du root motion). Pour reconstruire, le repère de repos du contrôleur est rapporté à celui du DEF ; le parent évalué est ensuite retiré via la conversion locale Blender. L’inverse ne touche que les canaux des contrôleurs FK de la copie ; les matrices DEF sont vérifiées après évaluation. Les proportions viennent du rig cible. Une pose non réalisable est rejetée, de même qu’une source périmée, un mélange IK/FK, la NLA ou des contacts pour ce profil FK. Ce n’est ni un inverse Rigify IK général, ni un mapping SOMA.
- Les tests repartent des deux morphologies du banc Sprint 3, **Élancé/A** et **Trapu/T**, réajustées et générées avec Rigify. Sur 22 DEF et quatre images libres par rig, les résidus maximaux rapportés sont respectivement **0,825 µm / 6,36 µrad** et **0,900 µm / 4,12 µrad**. Ancres (sous-images comprises), clés source, slot et tête de lecture sont contrôlés ; le test de ce composant ne recuit pas la peau et ne valide pas l’export. C’est une reconstruction vers le squelette natif cible, pas un transfert de proportions entre ces deux rigs.
- Le [contrôle de contacts](../addon/animation/humanoid_contacts.py) reçoit aussi une copie candidate déjà remplie et détachée, sans régénérer les échantillons. Il protège les coordonnées des clés et les poses évaluées aux ancres, limite les ajouts aux transformations des contrôleurs explicitement autorisés et compare les contacts actifs entre source et copie par identifiant et influence. La revue indépendante a reproduit un défaut sur le premier ZIP : une copie pouvait masquer un contact en animant `mute`. Le correctif couvre ce cas et la disparition d’un contact par un autre chemin. L’appelant doit écarter une copie rejetée.
- La fixture **BVH originale** a été importée puis transposée en contrôleurs IK pour tester pied, main, prise à deux mains, accessoire mobile et plages disjointes. Le plus grand écart DEF à pleine influence est de **0,000118 unité Blender** ; un conflit à l’ancre **5,5** produit **1,316004 unité Blender** et un rejet sans modifier l’ancre ni l’accessoire.
- Ces deux chemins ne constituent pas encore une chaîne d’IK humanoïde générale : la qualification des contacts utilise les tolérances natives sur les échantillons, pas une conversion arbitraire des contacts v1. Elle ne simule pas la physique et ne compense pas tout le corps. Le passage d’une sortie SOMA à ces deux briques reste à développer et à qualifier en 5B.

## Banc canonique original

```sh
run=$(mktemp -d /tmp/opencode/sas-humanoid-bench.XXXXXXXX)
python3 backends/inbetweening/humanoid_bench.py "$run/results"
```

Trois clips procéduraux originaux : transition de garde, attaque à deux mains, esquive latérale. Chaque dossier contient `reference.json` (grille entière + sous-image 13,5), `sparse.json`, `linear.json`, `smooth.json`, une requête canonique `request.json` v1 et `metrics.json`. Cadence 30 i/s, 19 joints, ancres 1/13,5/25/37. Le gabarit n’est ni SOMA ni une capture Rigify. Des cibles plantées et deux prises animées sont construites géométriquement dans la référence ; les baselines n’utilisent pas le solveur de contacts. La validation sur rig réellement évalué est une étape séparée.

| Clip | RMS joint, Linear / Smooth | RMS trajectoire racine | Écart maximal pied à sa cible | Écart maximal prise à sa cible |
|---|---:|---:|---:|---:|
| Transition | 1,84 / 1,97 cm | 1,42 / 1,55 cm | 0,89 / 0,89 cm | — |
| Attaque deux mains | 5,36 / 5,35 cm | 3,17 / 3,19 cm | 0,65 / 0,64 cm | 18,88 / 18,88 cm |
| Esquive | 4,77 / 4,81 cm | 4,24 / 4,28 cm | 2,59 / 2,58 cm | — |

Les quatre ancres sont identiques par construction. Le rapport mesure aussi le glissement horizontal cumulé, les erreurs angulaires, les pics de vitesse et d’accélération par joint (différences finies sur intervalles réels, dont la sous-image), et le temps d’interpolation CPU hors E/S et FK. Un passage sur le portable a donné **4 à 9 ms par clip et par variante** ; ces timings varient avec la charge. En 5B, utiliser la même requête et les mêmes métriques après conversion vers le gabarit SAS, puis répéter sur le rig Blender évalué. Aucun score neuronal n’est fourni.

Pour brancher un résultat en 5B, remplacer l’identifiant de référence de `request.json` par le jeton unique du job, puis utiliser `humanoid_bench.score_external(clip, decoded_response, request_id=job_id)` après décodage borné. La mesure exige exactement les images libres et rejoint ensuite les ancres de référence. Les JSON `linear` et `smooth` sont des clips complets, pas des réponses v1 ; seule la fixture de test de branchement reconditionne ses images libres en une réponse.

## Profils du lanceur

Le chemin MLP conserve `sprint4_worker.py`, la taille du checkpoint (61 080 octets), le CPU et 64 Kio par sens. Le profil de qualification `test-json`, sans poids ni UI, exerce les échanges humanoïdes strictement bornés : **8 Mio** de requête, **5 Mio** de réponse, au plus 77 joints, 300 ancres ou échantillons de pose par champ, 16 contacts de 300 échantillons et 120 s. Le budget de pose dense repose sur `77 × 300 × 7 × ~26 octets` (nombre et ponctuation), avec marge pour l’enveloppe et, en entrée, pour les contacts. La borne d’octets reste indépendante des bornes de dimensions.

La commande provient du profil et de chemins locaux choisis explicitement, jamais du document. Le superviseur borne le journal et contrôle le délai et le cycle de vie de l’arbre indépendamment du polling Blender. Sous POSIX, les descendants du groupe sont arrêtés même après une sortie normale du leader ; un enfant qui se détache volontairement peut échapper au groupe. Sous Windows, le Job Object est implémenté mais non testé. Le lanceur est destiné à des workers de confiance : il ne fournit ni isolation réseau effective, ni quota disque au processus. L’enveloppe et les poses du profil de test ne qualifient pas les contacts et le bake Blender.

Les deux configurations qualifiées déclarent explicitement le CPU et la version d’échange. Ce n’est pas un détecteur de matériel : un futur profil devra choisir son périphérique de manière vérifiable, épingler son environnement, contrôler les fichiers de modèle attendus et refuser le repli implicite. Une validation de taille MLP ne vaut pas validation du snapshot Kimodo multi-fichiers.

## Correction numérique des raccords

`humanoid_continuity.correct` applique le résidu de racine et de rotation locale dans un voisinage borné, avec pondération quintique et quaternions sur le plus court arc. Les offsets hors racine restent fixes. Il faut connaître la prédiction brute **à chaque ancre exacte**, pas une image arrondie ; cet auxiliaire reste côté adaptateur, le résultat v1 n’inclut aucun échantillon d’ancre. Sans cette information, ne pas appliquer cette correction.

Sur les mêmes clips, `continuity-fixture.json` mesure un biais déterministe ajouté autour de 13,5. Les ancres exactes sont réinsérées avant **les deux** mesures, comme au Sprint 4 :

| Clip | Pic accél. racine avant → après (m/s²) | Pic accél. locale avant → après (rad/s²) |
|---|---:|---:|
| Transition | 597,4 → 20,9 | 1880,9 → 63,7 |
| Attaque deux mains | 595,9 → 22,3 | 1880,7 → 230,5 |
| Esquive | 596,8 → 21,6 | 1880,9 → 63,7 |

Le pic de vitesse angulaire de l’attaque, hors du principal raccord corrigé, reste à 19,63 rad/s. Cette amélioration sur un biais construit n’est ni une garantie de continuité entre clés Blender, ni un résultat d’IA, ni une résolution des contacts.

## Archive et vérification indépendante

Le ZIP local contient uniquement les 42 entrées du code, du manifeste et de `LICENSE` ; pas de poids, roue, fixture ou actif tiers. La relecture indépendante a vérifié l’installation hors ligne, la désactivation/réactivation et la désinstallation réelle ; sur le ZIP corrigé, elle a vérifié les quatre tests de contacts et reproduit le rejet de l’ancienne tentative de masquer `FOOT_R` à l’image 6. SHA-256 du ZIP corrigé livré localement : `9095cf3d43a2c0e1dfacff7fa5e5d33d78e5810258e3e3e25bfdeffb44f755b1`.

```sh
bash scripts/build_extension.sh
SAS_MOTION_TEST_PYTHON=/usr/bin/python3 SOULS_TEST_GODOT=1 bash scripts/test_headless.sh
bash tests/test_shell_failures.sh dist/souls_animation_studio-0.5.0-dev-sprint-5a.zip
```

Sur le ZIP corrigé : **113 tests réussis, 0 échec, 7 skips de répartition CPython/Blender** (les cas passent dans l’autre environnement). Hors décompte : preuve MLP entraînée (22 poses, ancres exactes, bake et réimport GLB), trois sondes Godot en projets temporaires, désinstallation et tests négatifs, tous réussis.

Le test négatif propage un ZIP corrompu et une assertion Blender au shell (code 1 attendu dans les deux cas). La sonde distincte Python 3.11.18 passe les 13 tests banc/continuité et 17 tests de transport ; un test NumPy est ignoré car NumPy n’est pas installé pour cet interpréteur. Entre Python 3.11.18 et 3.14.8, les 21 JSON produits par le banc sont numériquement équivalents (écart maximal de `2,84e-14`, hors chronométrage). La sonde de continuité historique Sprint 4 a également été rejouée avec les poids existants.

Défauts rencontrés pendant l’intégration : une première archive de travail avait une tolérance de rigidité trop stricte pour les matrices de repos Rigify (précision float32) ; le test a été repris sur les deux morphologies sans relaxer la validation des poses finales. Blender peut signaler l’absence de **MeshOptimizer** et du dépôt `blender_org` ; la compression MeshOptimizer n’est pas qualifiée, et la désinstallation effective est contrôlée séparément. La prise en charge de Windows, la recette en fenêtre et le rendu restent non vérifiés.

## Suite

Voir le [plan Sprint 5B](sprint-5b-plan.md) pour les licences distinctes, les révisions, les prérequis et le premier essai. Aucun code, poids, actif ou corpus Kimodo n’est embarqué. Les scripts et fixtures originaux du Sprint 5A relèvent de [GPL-3.0-or-later](../LICENSE).
