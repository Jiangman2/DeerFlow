# Portal icons, 2026-10-19

Created with the built-in `image_gen` tool, one call per asset, with genuine
transparent backgrounds. Reference: `public/images/action-image.png`.
Original generated PNGs are retained here. Web outputs use new versioned names.

Shared generation prompt: one production UI icon in the reference's rounded 3D
style, light blue to cobalt gradients, gentle highlights and soft shading, straight
on, generous transparent padding. Match its chunky friendly shapes, not line art.
No titles, labels, watermarks or background. Center the visible icon at about
56 percent of the canvas. For jump destinations, bake a tiny up-right outbound
arrow into the lower-right of the same bitmap, about 15 percent of the main symbol
width, in the same rounded white/light-blue material; it is a subordinate hint.
The More menu has no outbound arrow.

Subjects:

- `proofread`: blue-white document, blue text lines, cyan-green check mark.
- `translate`: two rounded blue-white tiles with A and 文.
- `liveTranscribe`: rounded blue microphone and three audio-wave bars.
- `convert`: two blue-white documents connected by a short curved conversion arrow.
- `recordTranscribe`: blue headset and pale-blue audio waveform.
- `speech`: blue loudspeaker and two thick curved sound waves.
- `ppt`: blue presentation slide, orange chart inset and white chart.
- `more`: four rounded blue 3D tiles in a two-by-two grid, no arrow.

Targeted second edit for proofread and translate: change only the outbound arrow
to roughly 15 percent of the main symbol width and height. Preserve the main
subject, blue gradients, 3D material, lighting, transparent padding and composition.
No extra badges or background.

`build-artwork.cjs` only normalizes pixel bounds and encodes WebP; it never draws
or overlays the arrows. It matches the reference's measured alpha bounds, emits
192-pixel transparent canvases and bundles the small image data into the UI to
avoid a separate image request or image-optimizer dependency. See `manifest.json`
for original and optimized hashes, dimensions and byte counts.

## Contrast and visual centering revision

Use case: precise-object-edit. Edit the supplied transparent 3D UI icon. Change ONLY the small outbound up-right arrow in the lower-right corner. It was too pale: recolor the arrow to saturated warm amber/orange, approximately #F58A23 highlight and #CB5C13 shaded edges, so it is clearly recognizable against both the blue and pale-white main icon at small UI sizes. Give the arrow the SAME rounded 3D volume/material/lighting as the main artwork, no separate badge or white square. Keep the arrow small, about 18-20 percent of the main symbol's width, slightly bolder rather than a hairline. Preserve the main symbol EXACTLY: subject, internal conversion arrows or sound waves, letters, blue/white colors, lighting, composition, padding, overall visible size and position remain unchanged. Do not recolor any internal main-symbol arrow. Maintain the genuine transparent background and do not add any text, decoration, watermark, backdrop, or shadow outside the existing object. This is one member of a consistent blue 3D icon family, with only the navigation hint colored orange.

Original image-generation, PPT-generation, research and analysis pixels are preserved. Their transparent padding is normalized alongside the generated icons, so visible artwork and text can be centered as one group. The web canvases are now 128 pixels with 124-pixel visible extents and a two-pixel transparent gutter. Prior images and delivery remain preserved in the sibling 20261019 folder.
