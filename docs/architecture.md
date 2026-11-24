# Architecture

## Code installé dans Blender

| Dossier | Responsabilité |
| --- | --- |
| `addon/ui/`, `preferences.py` | Affichage, préférences, état des actions. Aucun traitement des poses ou du maillage. |
| `addon/operators/` | Validation du contexte Blender, orchestration des opérations, erreurs et annulation. |
| `addon/rigging/` | Squelette de déformation, skinning et contrôles ; aucune dépendance à l’interface. |
| `addon/animation/` | Lecture des poses, retargeting, contacts, validation et application en action Blender. |
| `addon/exporters/` | Export du squelette de déformation et des canaux d’animation cuits, sans importer les contrôleurs dans Godot. |

Au Sprint 0, seuls l’enregistrement, le panneau, les préférences et les diagnostics sont actifs. Les autres modules sont réservés. `tests/`, `docs/`, `scripts/` et `backends/` ne font pas partie du ZIP.

## Moteurs remplaçables — conception, pas une API implémentée

Un futur opérateur délèguera à un client léger, sans importer de bibliothèques ML dans Blender. Chaque adaptateur de `backends/` lancera un moteur local dans son propre environnement Python, avec un exécutable choisi explicitement. Pas de service distant ni de téléchargement automatique.

Le contrat à prototyper est une CLI locale et des entrées/sorties de fichiers : en-tête JSON versionné et formats de données non exécutables, dans un dossier de travail temporaire. Une demande précisera opération, unités, axes, hiérarchie, pose de repos, fréquence, poses verrouillées et contacts si applicables. Une réponse contiendra données produites, avertissements et métadonnées du moteur (version, poids, seed) ou une erreur explicite. Prévoir timeout, annulation et validation des tailles et valeurs avant tout changement de scène.

- **Rigging** : conversion du résultat vers une hiérarchie humanoïde stable ; séparation entre os de déformation et contrôleurs. Vérifier indices, topologie et poids normalisés.
- **Animation** : reconstruire les poses sur le rig cible ; conserver les valeurs originales aux images clés et limiter la correction des contacts aux images non verrouillées. Signaler les conflits insolubles, par exemple un pied à la fois verrouillé hors sol et déclaré en contact. Tester pied-sol et main-accessoire séparément.
- **Export** : tester un GLB/glTF et son squelette de déformation sur un projet Godot de test, jamais dans le jeu de l’utilisateur.
