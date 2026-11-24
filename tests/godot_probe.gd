# SPDX-License-Identifier: GPL-3.0-or-later
extends SceneTree

const EPSILON := 0.02 # meters, to allow importer floating-point differences


func _initialize() -> void:
	call_deferred("_run")


func fail(message: String) -> void:
	printerr("GLB import probe: ", message)
	quit(1)


func _run() -> void:
	var args := OS.get_cmdline_user_args()
	if args.size() < 1 or args.size() > 3:
		fail("expected a GLB path, optional JSON reference, and optional accessory GLB")
		return

	var reference: Dictionary = {}
	if args.size() >= 2:
		var parsed: Variant = JSON.parse_string(FileAccess.get_file_as_string(args[1]))
		if not parsed is Dictionary:
			fail("the JSON reference is missing or malformed: " + args[1])
			return
		reference = parsed
		if not valid_reference(reference):
			fail("the JSON reference does not contain valid bounds, duration and joint information")
			return
	if reference.has("accessory") != (args.size() == 3):
		fail("accessory comparison requires both accessory reference data and a separate GLB")
		return
	if args.size() == 3 and not valid_accessory_reference(reference["accessory"]):
		fail("invalid accessory reference data")
		return
	var bone_name: String = reference.get("bone", "DEF-upper_arm.L")
	var document := GLTFDocument.new()
	var state := GLTFState.new()
	var status := document.append_from_file(args[0], state)
	if status != OK:
		fail("GLTFDocument.append_from_file returned " + error_string(status))
		return
	var imported := document.generate_scene(state, float(reference["accessory"]["fps"]) if args.size() == 3 else 30.0)
	if imported == null:
		fail("GLTFDocument.generate_scene did not generate a scene")
		return
	root.add_child(imported)

	var meshes := imported.find_children("*", "MeshInstance3D", true, false)
	var skeletons := imported.find_children("*", "Skeleton3D", true, false)
	var players := imported.find_children("*", "AnimationPlayer", true, false)
	var has_accessory := args.size() == 3
	if meshes.size() != (2 if has_accessory else 1) or skeletons.size() != 1 or players.is_empty():
		fail("unexpected mesh, skeleton or AnimationPlayer count")
		return
	var mesh_instance: MeshInstance3D
	var accessory: MeshInstance3D
	for node: MeshInstance3D in meshes:
		if node.skin != null:
			mesh_instance = node
		else:
			accessory = node
	if mesh_instance == null or (has_accessory and accessory == null):
		fail("expected one skinned character and, in combined mode, one unskinned accessory")
		return
	var skeleton: Skeleton3D = skeletons[0]
	var skin := mesh_instance.skin
	var target_index := skeleton.find_bone(bone_name)
	if skin == null or mesh_instance.get_node_or_null(mesh_instance.skeleton) != skeleton:
		fail("imported mesh has no skin referencing the imported skeleton")
		return
	if target_index < 0 or skeleton.get_bone_count() <= 10:
		fail("expected a humanoid skeleton with the deform bone " + bone_name)
		return
	var skin_has_arm := false
	if skin.get_bind_count() != int(reference.get("joint_count", skin.get_bind_count())):
		fail("Godot did not preserve the glTF joint bind count")
		return
	for i in range(skin.get_bind_count()):
		var bind_name := skin.get_bind_name(i)
		var bone := skeleton.find_bone(bind_name) if bind_name else skin.get_bind_bone(i)
		if bone < 0 or bone >= skeleton.get_bone_count() or not skin.get_bind_pose(i).is_finite():
			fail("invalid imported skin bind #" + str(i))
			return
		skin_has_arm = skin_has_arm or bone == target_index
	if not skin_has_arm:
		fail("the deform arm exists but is not bound to the skin")
		return
	if mesh_instance.mesh == null or not valid_surface_weights(mesh_instance.mesh, skin.get_bind_count()):
		fail("imported mesh has missing or invalid bone indices/vertex weights")
		return

	var box := mesh_instance.global_transform * mesh_instance.get_aabb()
	if not reference.is_empty():
		var lower: Array = reference["aabb_min"]
		var upper: Array = reference["aabb_max"]
		var expected_min := Vector3(lower[0], lower[1], lower[2])
		var expected_max := Vector3(upper[0], upper[1], upper[2])
		if box.position.distance_to(expected_min) > EPSILON or box.end.distance_to(expected_max) > EPSILON:
			fail("axis/origin/scale mismatch: actual %s, expected %s to %s" % [box, expected_min, expected_max])
			return
	else:
		# Standalone mode assumes the upright, meter-scale SAS reference mannequin.
		if not (1.5 < box.size.y and box.size.y < 2.1 and box.size.z < box.size.x and box.size.x < box.size.y):
			fail("unexpected axis or scale for the upright reference humanoid: " + str(box))
			return

	for player_node in players:
		var player: AnimationPlayer = player_node
		for clip in player.get_animation_list():
			var animation := player.get_animation(clip)
			if animation.length <= 0.0:
				continue
			if reference.has("duration") and absf(animation.length - float(reference["duration"])) > 0.01:
				continue
			for track in range(animation.get_track_count()):
				if is_varying_arm_track(animation, track, bone_name):
					player.play(clip)
					player.seek(0.0, true)
					var pose_start := skeleton.get_bone_pose_rotation(target_index)
					player.seek(animation.length * 0.9, true)
					var pose_end := skeleton.get_bone_pose_rotation(target_index)
					if absf(pose_start.dot(pose_end)) >= 0.999:
						fail("the varying rotation track did not move the imported deform bone")
						return
					if has_accessory:
						var issue := check_accessory(accessory, skeleton, player, reference["accessory"], args[2])
						if not issue.is_empty():
							fail(issue)
							return
					print("Godot %s: %d bones, %d binds, AABB %s; %s (%ss) moves %s" % [
						Engine.get_version_info().string, skeleton.get_bone_count(),
						skin.get_bind_count(), box, clip, animation.length, bone_name,
					])
					quit(0)
					return
	fail("no imported animation contains a nonconstant rotation channel for " + bone_name)


func vec(value: Array) -> Vector3:
	return Vector3(value[0], value[1], value[2])


func transform_from(columns: Array) -> Transform3D:
	return Transform3D(Basis(vec(columns[0]), vec(columns[1]), vec(columns[2])), vec(columns[3]))


func basis_error(actual: Basis, expected: Basis) -> float:
	return maxf(actual.x.distance_to(expected.x),
		maxf(actual.y.distance_to(expected.y), actual.z.distance_to(expected.z)))


func valid_accessory_reference(value: Variant) -> bool:
	if not value is Dictionary or not value.get("parent") is String \
			or not value.get("fps") is float or not is_finite(value["fps"]) or value["fps"] <= 0.0:
		return false
	if not valid_vector(value.get("aabb_min")) or not valid_vector(value.get("aabb_max")):
		return false
	var samples: Variant = value.get("samples")
	if not samples is Array or samples.size() < 2:
		return false
	var last := -1.0
	for sample in samples:
		if not sample is Dictionary or not sample.get("time") is float \
				or not is_finite(sample["time"]) or sample["time"] < 0.0 or sample["time"] <= last:
			return false
		last = sample["time"]
		for key in ["hand", "world", "relative"]:
			var columns: Variant = sample.get(key)
			if not columns is Array or columns.size() != 4:
				return false
			for column in columns:
				if not valid_vector(column):
					return false
	return true


func valid_vector(value: Variant) -> bool:
	if not value is Array or value.size() != 3:
		return false
	for component in value:
		if not component is float or not is_finite(component):
			return false
	return true


func check_accessory(accessory: MeshInstance3D, skeleton: Skeleton3D, player: AnimationPlayer,
		reference: Dictionary, asset_path: String) -> String:
	var bone_name: String = reference["parent"]
	var index := skeleton.find_bone(bone_name)
	var parent := accessory.get_parent()
	while parent != null and not parent is BoneAttachment3D:
		parent = parent.get_parent()
	if index < 0 or parent == null or parent.bone_name != bone_name or parent.get_parent() != skeleton:
		return "accessory is not attached to the expected DEF bone: " + bone_name

	var state := GLTFState.new()
	var document := GLTFDocument.new()
	if document.append_from_file(asset_path, state) != OK:
		return "could not import the separate GLB"
	var asset := document.generate_scene(state) as Node3D
	if asset == null:
		return "the separate GLB has no 3D scene"
	var mesh_nodes := asset.find_children("*", "MeshInstance3D", true, false)
	if asset is MeshInstance3D:
		mesh_nodes.push_front(asset)
	if mesh_nodes.size() != 1 or not asset.find_children("*", "Skeleton3D").is_empty() \
			or not asset.find_children("*", "AnimationPlayer").is_empty():
		asset.free()
		return "separate GLB must contain exactly one rigid mesh, no rig or animation"
	var replacement: MeshInstance3D = mesh_nodes[0]
	var original_box := accessory.get_aabb()
	var box := replacement.get_aabb()
	if replacement.skin != null or replacement.mesh == null or box.position.distance_to(original_box.position) > 0.0001 \
			or box.end.distance_to(original_box.end) > 0.0001 \
			or box.position.distance_to(vec(reference["aabb_min"])) > 0.0001 \
			or box.end.distance_to(vec(reference["aabb_max"])) > 0.0001:
		asset.free()
		return "separate GLB geometry/pivot disagrees with Blender or the combined accessory"
	# Keep the combined animated pivot, replacing only the mesh at identity.
	accessory.add_child(asset)
	accessory.mesh = null
	var worst_world := Vector2.ZERO
	var worst_relative := Vector2.ZERO
	var worst_hand := Vector2.ZERO
	var samples: Array = reference["samples"]
	for sample: Dictionary in samples:
		player.seek(float(sample["time"]), true)
		skeleton.force_update_all_bone_transforms()
		# The deferred scene-tree skeleton notification has not run during this synchronous probe.
		parent.on_skeleton_update()
		var hand := skeleton.global_transform * skeleton.get_bone_global_pose(index)
		var world := accessory.global_transform
		if not hand.is_finite() or not world.is_finite() or absf(hand.basis.determinant()) < 0.000001:
			return "non-finite animation sample or singular DEF transform"
		if not replacement.global_transform.is_equal_approx(world):
			return "separate GLB does not reuse the animated pivot at identity"
		var relative := hand.affine_inverse() * world
		var expected_hand := transform_from(sample["hand"])
		var expected_world := transform_from(sample["world"])
		var expected_relative := transform_from(sample["relative"])
		worst_hand = worst_hand.max(Vector2(hand.origin.distance_to(expected_hand.origin),
			basis_error(hand.basis, expected_hand.basis)))
		worst_world = worst_world.max(Vector2(world.origin.distance_to(expected_world.origin),
			basis_error(world.basis, expected_world.basis)))
		worst_relative = worst_relative.max(Vector2(relative.origin.distance_to(expected_relative.origin),
			basis_error(relative.basis, expected_relative.basis)))
	var summary := "%d samples, max (meters, basis): world %s, DEF-relative %s, DEF %s" % [
		samples.size(), worst_world, worst_relative, worst_hand]
	if worst_world.x > 0.002 or worst_world.y > 0.008 or worst_hand.x > 0.008 \
			or worst_hand.y > 0.045 or worst_relative.x > 0.012 or worst_relative.y > 0.045:
		return "Blender/Godot transform mismatch: " + summary
	print("Accessory and separate GLB reuse: " + summary)
	return ""


func valid_reference(reference: Dictionary) -> bool:
	if not reference.get("bone") is String or not reference.get("duration") is float \
			or not reference.get("joint_count") is float:
		return false
	for key in ["aabb_min", "aabb_max"]:
		if not valid_vector(reference.get(key)):
			return false
	return true


func valid_surface_weights(mesh: Mesh, bind_count: int) -> bool:
	if mesh.get_surface_count() < 1:
		return false
	for i in range(mesh.get_surface_count()):
		var arrays := mesh.surface_get_arrays(i)
		if arrays[Mesh.ARRAY_BONES] == null or arrays[Mesh.ARRAY_WEIGHTS] == null:
			return false
		var vertices: PackedVector3Array = arrays[Mesh.ARRAY_VERTEX]
		var bones: PackedInt32Array = arrays[Mesh.ARRAY_BONES]
		var weights: PackedFloat32Array = arrays[Mesh.ARRAY_WEIGHTS]
		var count := vertices.size()
		var influence_count := 8 if bones.size() == 8 * count else 4
		if count == 0 or bones.size() != influence_count * count or weights.size() != bones.size():
			return false
		for vertex in range(count):
			var total := 0.0
			for slot in range(influence_count):
				var item := vertex * influence_count + slot
				var weight := weights[item]
				if not is_finite(weight) or weight < 0.0 or weight > 1.0:
					return false
				if weight > 0.0 and (bones[item] < 0 or bones[item] >= bind_count):
					return false
				total += weight
			if absf(total - 1.0) > 0.002:
				return false
	return true


func is_varying_arm_track(animation: Animation, track: int, bone_name: String) -> bool:
	if animation.track_get_type(track) != Animation.TYPE_ROTATION_3D:
		return false
	if not animation.track_is_enabled(track):
		return false
	var path := animation.track_get_path(track)
	if path.get_subname_count() != 1 or path.get_subname(0) != bone_name:
		return false
	var count := animation.track_get_key_count(track)
	if count < 2:
		return false
	var first: Quaternion = animation.track_get_key_value(track, 0)
	for key in range(1, count):
		var next: Quaternion = animation.track_get_key_value(track, key)
		if absf(first.dot(next)) < 0.999:
			return true
	return false
