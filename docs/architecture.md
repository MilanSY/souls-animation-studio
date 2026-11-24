# Architecture

## Code installé dans Blender

| Dossier | Responsabilité |
| --- | --- |
| `addon/ui/`, `preferences.py` | Affichage, préférences, état des actions. Aucun traitement des poses ou du maillage. |
| `addon/operators/` | Validation du contexte Blender, orchestration des opérations, erreurs et annulation. |
| `addon/rigging/` | Squelette de déformation, skinning et contrôles ; aucune dépendance à l’interface. |
| `addon/animation/cpu.py` | Types de données et backend CPU pur Python : scalaires, quaternions, échantillons. |
| `addon/animation/blender.py` | Lecture des poses de l’Action et du slot actif, copie et écriture des courbes Blender. |
| `addon/exporters/` | Export du squelette de déformation et des canaux d’animation cuits, sans importer les contrôleurs dans Godot. |
| `addon/contacts/` | Graphe de contacts persistant sur les contrôleurs IK, ancrages, vérification de cycles et mesure sur les os DEF. |
| `addon/animation/constraints.py` | Évaluation de la proposition d’animation sur le rig Blender et collecte des résidus ; indépendant du calcul d’interpolation. |
| `addon/poses/` | Capture et application des canaux de contrôle, mélange, symétrie rig-local, sérialisation JSON. |

Rigify est appelé à la demande depuis la couche rigging. Le skinning calcule des poids sur une copie de travail ; seuls les groupes DEF contrôlés et le modificateur sont appliqués au maillage choisi. L’export construit une armature et une peau jetables, puis les nettoie et restaure mode, sélection et image. `tests/`, `docs/`, `scripts/`, `demo/` et `backends/` ne font pas partie du ZIP.

### Graphe de contacts et évaluation

Pour un contact, un Empty mémorise l’ancrage dans le monde ou relativement à une cible externe. Le widget IK suit en coordonnées monde la position et éventuellement la rotation de cet ancrage. Les bornes d’influence sont évaluées par des expressions Blender simples, sans handler, script embarqué ni dépendance à l’action du rig. Le résidu est mesuré sur l’os DEF évalué : une cible de widget atteinte ne suffit pas.

La chaîne d’un accessoire à deux mains est **contrôleur primaire → accessoire → ancrage secondaire → contrôleur secondaire**. La main secondaire ne pilote jamais l’accessoire. Les dépendances déjà présentes (parentage, contraintes et entrées évaluables connues) sont contrôlées avant création ; remettre l’accessoire en place avant de construire la chaîne. Le système n’est pas un analyseur exhaustif des expressions ou dépendances arbitraires ajoutées ensuite par un autre add-on.

Le backend pur propose les canaux des images intermédiaires. L’adaptateur de contraintes évalue ensuite l’Action candidate sur le rig, compare les matrices des poses verrouillées et rapporte les erreurs visibles après résolution native. L’action source, son slot et l’image courante sont restaurés. Les résidus ne deviennent pas des clés, et le solveur ne cherche pas automatiquement une trajectoire de compensation ou l’évitement de collisions.

## Moteurs remplaçables

Le backend CPU reçoit des poses numériques et renvoie des échantillons ; il ne reçoit ni objets Blender, ni paramètres d’UI, ni chemins de projet. Voir [`backends/inbetweening`](../backends/inbetweening/README.md). CondMDI, UniRig, les contacts et le retargeting ne sont pas intégrés ; une future intégration devra adapter ses données au squelette et définir ses propres entrées.

Un futur opérateur délèguera à un client léger, sans importer de bibliothèques ML dans Blender. Chaque adaptateur de `backends/` lancera un moteur local dans son propre environnement Python, avec un exécutable choisi explicitement. Pas de service distant ni de téléchargement automatique.

Le contrat à prototyper est une CLI locale et des entrées/sorties de fichiers : en-tête JSON versionné et formats de données non exécutables, dans un dossier de travail temporaire. Une demande précisera opération, unités, axes, hiérarchie, pose de repos, fréquence, poses verrouillées et contacts si applicables. Une réponse contiendra données produites, avertissements et métadonnées du moteur (version, poids, seed) ou une erreur explicite. Prévoir timeout, annulation et validation des tailles et valeurs avant tout changement de scène.

- **Rigging** : conversion du résultat vers une hiérarchie humanoïde stable ; séparation entre os de déformation et contrôleurs. Vérifier indices, topologie et poids normalisés.
- **Animation** : reconstruire les poses sur le rig cible ; conserver les valeurs originales aux images clés et limiter la correction des contacts aux images non verrouillées. Signaler les conflits insolubles, par exemple un pied à la fois verrouillé hors sol et déclaré en contact. Tester pied-sol et main-accessoire séparément.
- **Export** : tester un GLB/glTF et son squelette de déformation sur un projet Godot de test, jamais dans le jeu de l’utilisateur.

## Provenance et licences

- L’extension, la topologie de référence, les poses de démonstration et les scripts/fixtures écrits ici sont fournis sous [GPL-3.0-or-later](../LICENSE). `scripts/reference_character.py` et `scripts/build_demo.py` sont les sources de génération des données de démonstration.
- Les nouvelles données du Sprint 2 (géométrie et matériaux de l’épée fictive, huit poses, animation et prises) sont construites par `scripts/build_combat_demo.py`. Le JSON ne contient que des noms et des transformations ; aucun script, asset de marketplace, texture ou dépendance liée.
- Le `.blend` contient des données générées par **Rigify 0.6.10**, © Blender Foundation et contributeurs, **GPL-2.0-or-later** ([sources Blender](https://projects.blender.org/blender/blender), build `d13f752e3b9c`, `scripts/addons_core/rigify`). Le fichier contient les armatures, contraintes, pilotes et widgets générés, mais pas le code Python du panneau Rigify. Les composants d’origine compatibles GPL qui se reflètent dans ces données sont distribués ici sous GPL-3.0-or-later. Rigify lui-même n’est pas dans le ZIP. **glTF-Blender-IO** (Apache-2.0) est appelé via Blender, sans copie de code.
- Aucun poids, ensemble de données, maillage importé, texture tierce ni binaire Blender/Godot n’est redistribué.
