# Procédure de validation visuelle

Les tests numériques détectent un rig ou un export cassé. Ils ne valident pas le volume, le pli de la peau ou le contact au sol.

## Blender 5.2.2

1. Ouvrir `demo/sas-humanoid.blend`, sans autoriser Auto Run Python Scripts. En vue solide, cadrer le personnage et comparer les vues avant, côté et trois-quarts à l’image **1**. À **13**, vérifier les deux genoux et coudes pendant l’accroupissement ; à **25**, vérifier la jambe en appui et les poignets pendant le pas. Scrubber les images intermédiaires, puis comparer **1** et **37**. Activer alternativement **Overlays** et **Wireframe** pour isoler la surface et ses boucles.
2. Noter couverture des joints, plis pincés, perte de volume, intersections, sauts de twist, sol traversé et pied qui glisse. Tester des flexions progressivement jusqu’à environ 90° par joint, gauche puis droite, sans en conclure qu’une amplitude supérieure est couverte.
3. Sur une copie du fichier, détacher l’Action. En mode Pose, déplacer séparément les quatre contrôles IK, puis tester FK (voir guide), bassin, colonne, tête, orientation des pieds et des mains. Tester **Reset Pose** et Undo. Vérifier qu’un groupe DEF volontairement vidé est signalé par **Validate Rig**.
4. Sur une copie non liée du mannequin, exécuter la voie manuelle. Déplacer un coude en mode Édition, générer et vérifier que le changement d’axe est conservé. Tester le chemin All-in-One séparément.
5. Dans l’Éditeur d’actions, choisir `SAS Demo - Authored Keys` et générer les deux modes (plage 1–37, clés 1, 13, 25, 37). Comparer les poses clés, puis jouer les clips et comparer le timing.

## Godot — projet indépendant

Dans un projet vierge, copier `demo/sas-humanoid.glb` dans `res://`, attendre l’import, ouvrir la scène importée et afficher `Skeleton3D`, le maillage et `AnimationPlayer`. Vérifier la hauteur de référence (~1,78 m), les pieds proches de Y=0, l’avant vers +Z, l’absence de contrôleurs et une flexion visible à la lecture du clip. Comparer à Blender, notamment à t=0, 0,5, 1,0 et 1,5 s (24 i/s). Tester le renderer **Compatibility** sur le matériel cible ; la validation headless n’exerce pas l’affichage standard.

Consigner la version, le GPU, les vues examinées, les images/angles en défaut et les vérifications qui n’ont pas été effectuées.
