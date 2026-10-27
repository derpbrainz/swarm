/**
 * Isometric 2.5D HTML5 Canvas Renderer with Shared Co-op Fog of War,
 * 3D-extruded walls/doors, Spaceship drop bay, sight cones, and sound rings.
 */

import { TILE_W, TILE_H, WALL_HEIGHT_PX, worldToIso, computeVisibilityPolygon } from './iso.js';

export class IsometricRenderer {
  constructor(canvas, classesConfig = {}) {
    this.canvas = canvas;
    this.ctx = canvas.getContext('2d');
    this.classesConfig = classesConfig;
    this.exploredCells = new Set();
    this.lastMissionId = null;
    this.fogCanvas = document.createElement('canvas');
    this.fogCtx = this.fogCanvas.getContext('2d');
    this.playerAnim = new Map();
    this.lastRenderTime = 0;
    this.currentDt = 0.016;
  }

  resetExplored() {
    this.exploredCells.clear();
  }

  setClassesConfig(cfg) {
    this.classesConfig = cfg || {};
  }

  render(state, localPlayerId, mouseWorld, camera) {
    const ctx = this.ctx;
    const W = this.canvas.width;
    const H = this.canvas.height;

    const now = typeof performance !== 'undefined' ? performance.now() : Date.now();
    this.currentDt = this.lastRenderTime ? Math.min(0.1, Math.max(0.001, (now - this.lastRenderTime) / 1000)) : 0.016;
    this.lastRenderTime = now;

    ctx.fillStyle = '#05080e';
    ctx.fillRect(0, 0, W, H);

    if (!state || !state.map) return;

    const mapKey = state.map.level_id || state.map.mission_id;
    if (this.lastMissionId !== mapKey && state.mission_time < 1.0) {
      this.exploredCells.clear();
      this.lastMissionId = mapKey;
    }

    // 1. Compute active visibility polygons for all living players (Shared Team Vision)
    const visions = [];
    const walls = state.map.walls || [];
    for (const p of state.players || []) {
      if (p.hp <= 0) continue;
      const cls = this.classesConfig[p.class_id];
      let range = cls ? cls.sight_range : 12.5;
      const inv = p.inventory;
      if (inv) {
        const eq = inv.equipped || inv;
        const hasLight =
          (eq.quick_use && eq.quick_use.template_id === 'tactical_light') ||
          (inv.storage || []).some((it) => it && it.template_id === 'tactical_light');
        if (hasLight) range += 4.5;
      }
      const poly = computeVisibilityPolygon(p.x, p.y, range, walls, 84);
      visions.push({ player: p, range, poly });

      // Mark 2x2m floor cells within sight range & polygon bounding area as explored
      const minX = Math.max(0, Math.floor((p.x - range) / 2) * 2);
      const maxX = Math.min(state.map.width, Math.ceil((p.x + range) / 2) * 2);
      const minY = Math.max(0, Math.floor((p.y - range) / 2) * 2);
      const maxY = Math.min(state.map.height, Math.ceil((p.y + range) / 2) * 2);
      for (let gx = minX; gx < maxX; gx += 2) {
        for (let gy = minY; gy < maxY; gy += 2) {
          if (Math.hypot(gx + 1 - p.x, gy + 1 - p.y) <= range * 0.92) {
            this.exploredCells.add(`${gx},${gy}`);
          }
        }
      }
    }

    // 2. Draw floor segments & grid
    this.drawFloorSegments(ctx, state.map, camera);

    // 2b. Draw floor wiring from puzzle consoles to bulkhead doors
    if (state.map.wires && state.map.wires.length > 0) {
      this.drawWires(ctx, state.map.wires, camera);
    }

    // 3. Draw Docked Spaceship & Extraction Zone
    if (state.map.spaceship) {
      this.drawSpaceship(ctx, state.map.spaceship, camera);
    }
    if (state.map.extraction_zone) {
      this.drawExtractionZone(ctx, state.map.extraction_zone, camera);
    }

    // 4. Draw Sound Rings & Deployables (Healing Fields, Sonar Pulses)
    this.drawSounds(ctx, state.sounds || [], camera);
    this.drawGroundDeployables(ctx, state.deployables || [], camera);

    // 5. Draw Puzzle Terminals, Supply Crates/Body Chests & Loot Pickups
    const localPlayer = (state.players || []).find((p) => p.id === localPlayerId);
    this.drawLoot(ctx, state.map.loot || [], localPlayer, camera);
    this.drawCrates(ctx, state.map.crates || [], localPlayer, camera);
    this.drawPuzzles(ctx, state.map.puzzles || [], localPlayer, camera);

    // 6. Depth-sorted 2.5D entities (walls, doors, turrets, enemies, players)
    const renderables = [];

    for (const w of walls) {
      if (w.active === false) continue;
      renderables.push({
        depth: w.x + w.w * 0.5 + (w.y + w.h * 0.5),
        kind: 'wall',
        obj: w,
      });
    }

    for (const dep of state.deployables || []) {
      if (dep.type === 'turret') {
        renderables.push({
          depth: dep.x + dep.y,
          kind: 'turret',
          obj: dep,
        });
      }
    }

    for (const e of state.enemies || []) {
      if (!e.visible) continue;
      renderables.push({
        depth: e.x + e.y,
        kind: 'enemy',
        obj: e,
      });
    }

    for (const p of state.players || []) {
      renderables.push({
        depth: p.x + p.y,
        kind: 'player',
        obj: p,
      });
    }

    renderables.sort((a, b) => a.depth - b.depth);

    for (const item of renderables) {
      if (item.kind === 'wall') {
        this.drawIsoBox(ctx, item.obj, camera);
      } else if (item.kind === 'turret') {
        this.drawTurret(ctx, item.obj, camera);
      } else if (item.kind === 'enemy') {
        this.drawEnemy(ctx, item.obj, camera);
      } else if (item.kind === 'player') {
        this.drawPlayer(ctx, item.obj, item.obj.id === localPlayerId, mouseWorld, camera);
      }
    }

    // 7. Draw Projectiles
    this.drawProjectiles(ctx, state.projectiles || [], camera);

    // 8. Draw Shared Co-op Fog of War Overlay
    this.drawFogOfWar(ctx, state.map, visions, camera);

    // 8b. Re-draw visible/nearby Loot & Crate HUD markers above fog/walls so items are always easy to spot
    this.drawLoot(ctx, state.map.loot || [], localPlayer, camera, true);
    this.drawCrates(ctx, state.map.crates || [], localPlayer, camera, true);

    // 9. Draw Crosshair at Mouse World Position
    if (mouseWorld && localPlayer && !localPlayer.downed) {
      const mIso = worldToIso(mouseWorld.x, mouseWorld.y, camera);
      ctx.save();
      ctx.strokeStyle = 'rgba(56, 189, 248, 0.85)';
      ctx.lineWidth = 1.5;
      ctx.beginPath();
      ctx.arc(mIso.x, mIso.y, 9, 0, Math.PI * 2);
      ctx.moveTo(mIso.x - 14, mIso.y);
      ctx.lineTo(mIso.x + 14, mIso.y);
      ctx.moveTo(mIso.x, mIso.y - 14);
      ctx.lineTo(mIso.x, mIso.y + 14);
      ctx.stroke();
      ctx.restore();
    }
  }

  drawIsoQuad(ctx, x, y, w, h, camera, fillStyle, strokeStyle = null) {
    const p0 = worldToIso(x, y, camera);
    const p1 = worldToIso(x + w, y, camera);
    const p2 = worldToIso(x + w, y + h, camera);
    const p3 = worldToIso(x, y + h, camera);

    ctx.beginPath();
    ctx.moveTo(p0.x, p0.y);
    ctx.lineTo(p1.x, p1.y);
    ctx.lineTo(p2.x, p2.y);
    ctx.lineTo(p3.x, p3.y);
    ctx.closePath();
    if (fillStyle) {
      ctx.fillStyle = fillStyle;
      ctx.fill();
    }
    if (strokeStyle) {
      ctx.strokeStyle = strokeStyle;
      ctx.stroke();
    }
  }

  drawFloorSegments(ctx, map, camera) {
    const themeColors = {
      hangar: '#111927',
      corridor: '#131c2b',
      biolab: '#0f2224',
      reactor: '#1f1621',
      command: '#141e30',
      extraction: '#12241e',
    };

    for (const seg of map.segments || []) {
      const baseColor = themeColors[seg.theme] || '#121a28';
      this.drawIsoQuad(ctx, seg.x, seg.y, seg.w, seg.h, camera, baseColor, '#1e293b');

      // Subtle 2-unit isometric floor grid
      ctx.lineWidth = 1;
      for (let gx = seg.x; gx < seg.x + seg.w; gx += 2) {
        for (let gy = seg.y; gy < seg.y + seg.h; gy += 2) {
          const isExplored = this.exploredCells.has(`${gx},${gy}`);
          if (!isExplored) continue;
          this.drawIsoQuad(ctx, gx, gy, 2, 2, camera, null, 'rgba(148, 163, 184, 0.08)');
        }
      }

      // Segment title watermark on floor
      const centerIso = worldToIso(seg.x + seg.w * 0.5, seg.y + 1.8, camera);
      ctx.save();
      ctx.fillStyle = 'rgba(148, 163, 184, 0.35)';
      ctx.font = '600 12px Inter, system-ui, sans-serif';
      ctx.textAlign = 'center';
      ctx.fillText(`SECTOR 0${seg.index + 1}: ${seg.name.toUpperCase()}`, centerIso.x, centerIso.y);
      ctx.restore();
    }

    // Draw flat grid-aligned connector threshold markings
    for (const conn of map.connectors || []) {
      const isVert = conn.dir === 'E' || conn.dir === 'W';
      const cw = isVert ? 1.6 : 4.0;
      const ch = isVert ? 4.0 : 1.6;
      this.drawIsoQuad(
        ctx,
        conn.x - cw * 0.5,
        conn.y - ch * 0.5,
        cw,
        ch,
        camera,
        'rgba(56, 189, 248, 0.14)',
        'rgba(56, 189, 248, 0.45)'
      );
    }
  }

  drawLoot(ctx, lootItems, localPlayer, camera, overlayPass = false) {
    const colors = {
      medkit: '#22c55e',
      stim_injector: '#4ade80',
      ammo_pack: '#f59e0b',
      heavy_ammo: '#fb923c',
      credits: '#38bdf8',
      isotope_cell: '#a855f7',
      tactical_light: '#fde047',
      portable_turret: '#38bdf8',
      sonar_beacon: '#c084fc',
      plasma_lmg: '#f87171',
      rail_carbine: '#60a5fa',
      service_pistol: '#94a3b8',
      machine_pistol: '#fbbf24',
      flare_revolver: '#fb7185',
    };
    const pulse = 0.5 + 0.5 * Math.sin(performance.now() * 0.006);
    for (const lt of lootItems) {
      if (lt.collected) continue;
      if (overlayPass && localPlayer) {
        const d = Math.hypot(localPlayer.x - lt.x, localPlayer.y - lt.y);
        const gx = Math.floor(lt.x / 2) * 2;
        const gy = Math.floor(lt.y / 2) * 2;
        if (d > 16.0 && !this.exploredCells.has(`${gx},${gy}`)) continue;
      }
      const iso = worldToIso(lt.x, lt.y, camera);
      const itemKey = lt.type || lt.template_id || lt.item_type || 'credits';
      const color = colors[itemKey] || '#38bdf8';
      ctx.save();

      // Glowing beacon ring under the item
      ctx.beginPath();
      ctx.ellipse(iso.x, iso.y, 14 + pulse * 4, 7 + pulse * 2, 0, 0, Math.PI * 2);
      ctx.fillStyle = 'rgba(56, 189, 248, 0.14)';
      ctx.fill();
      ctx.strokeStyle = color;
      ctx.lineWidth = 1.5;
      ctx.stroke();

      // Item case icon
      ctx.fillStyle = '#0f172a';
      ctx.fillRect(iso.x - 10, iso.y - 16, 20, 14);
      ctx.strokeStyle = color;
      ctx.lineWidth = 2;
      ctx.strokeRect(iso.x - 10, iso.y - 16, 20, 14);

      ctx.fillStyle = color;
      ctx.fillRect(iso.x - 4, iso.y - 11, 8, 4);

      const wStr = lt.weight ? ` (${lt.weight}wt)` : '';
      const label = `${lt.name || itemKey.toUpperCase()}${wStr}`;
      ctx.font = 'bold 11px Inter, monospace';
      ctx.textAlign = 'center';
      ctx.fillStyle = 'rgba(6, 9, 15, 0.85)';
      const tw = ctx.measureText(label).width + 8;
      ctx.fillRect(iso.x - tw * 0.5, iso.y - 31, tw, 14);
      ctx.fillStyle = color;
      ctx.fillText(label, iso.x, iso.y - 20);

      if (localPlayer && !localPlayer.downed) {
        const dist = Math.hypot(localPlayer.x - lt.x, localPlayer.y - lt.y);
        if (dist <= 2.8) {
          ctx.fillStyle = '#fde047';
          ctx.font = 'bold 12px monospace';
          ctx.fillText('[PRESS E OR CLICK TO PICK UP]', iso.x, iso.y - 35);
        }
      }
      ctx.restore();
    }
  }

  drawCrates(ctx, crates, localPlayer, camera, overlayPass = false) {
    for (const cr of crates) {
      if (overlayPass && localPlayer) {
        const d = Math.hypot(localPlayer.x - cr.x, localPlayer.y - cr.y);
        const gx = Math.floor(cr.x / 2) * 2;
        const gy = Math.floor(cr.y / 2) * 2;
        if (d > 16.0 && !this.exploredCells.has(`${gx},${gy}`)) continue;
      }
      const items = cr.items || [];
      const isBody = cr.type === 'body_chest';
      const locked = Boolean(cr.locked);
      const iso = worldToIso(cr.x, cr.y, camera);
      const stroke = isBody ? '#f87171' : locked ? '#f59e0b' : '#38bdf8';
      const fill = isBody ? '#450a0a' : locked ? '#451a03' : '#0f172a';

      ctx.save();
      ctx.beginPath();
      ctx.ellipse(iso.x, iso.y, 22, 11, 0, 0, Math.PI * 2);
      ctx.strokeStyle = stroke;
      ctx.lineWidth = 1.5;
      ctx.stroke();

      ctx.fillStyle = fill;
      ctx.fillRect(iso.x - 15, iso.y - 20, 30, 20);
      ctx.strokeStyle = stroke;
      ctx.lineWidth = 2;
      ctx.strokeRect(iso.x - 15, iso.y - 20, 30, 20);

      ctx.font = 'bold 11px monospace';
      ctx.textAlign = 'center';
      const countText = items.length > 0 ? ` [${items.length} ITEMS]` : ' [EMPTY]';
      const title = `${cr.name || cr.label || 'SUPPLY CRATE'}${countText}`;
      const tw = ctx.measureText(title).width + 8;
      ctx.fillStyle = 'rgba(6, 9, 15, 0.85)';
      ctx.fillRect(iso.x - tw * 0.5, iso.y - 35, tw, 14);
      ctx.fillStyle = stroke;
      ctx.fillText(title, iso.x, iso.y - 24);

      if (localPlayer && !localPlayer.downed) {
        const dist = Math.hypot(localPlayer.x - cr.x, localPlayer.y - cr.y);
        if (dist <= (cr.radius || 2.5) + 0.4) {
          ctx.fillStyle = locked ? '#fbbf24' : '#38bdf8';
          ctx.font = 'bold 12px monospace';
          ctx.fillText(
            locked ? '[PRESS E: SKILL CHECK TO UNLOCK]' : '[PRESS E: OPEN CHEST OVERLAY]',
            iso.x,
            iso.y - 39
          );
        }
      }
      ctx.restore();
    }
  }

  drawSpaceship(ctx, ship, camera) {
    // Dropship landing pad markings
    this.drawIsoQuad(
      ctx,
      ship.x - 0.4,
      ship.y - 0.4,
      ship.w + 0.8,
      ship.h + 0.8,
      camera,
      'rgba(245, 158, 11, 0.12)',
      'rgba(245, 158, 11, 0.45)'
    );
    // Ship interior deck
    this.drawIsoQuad(ctx, ship.x, ship.y, ship.w, ship.h, camera, '#1e293b', '#38bdf8');

    // Boarding ramp on East side
    const rampX = ship.x + ship.w;
    const rampY = ship.y + 0.7;
    const rampW = ship.ramp_open ? 2.0 : 0.4;
    const rampH = ship.h - 1.4;
    this.drawIsoQuad(
      ctx,
      rampX,
      rampY,
      rampW,
      rampH,
      camera,
      ship.ramp_open ? 'rgba(56, 189, 248, 0.28)' : 'rgba(239, 68, 68, 0.35)',
      ship.ramp_open ? '#38bdf8' : '#ef4444'
    );

    const labelIso = worldToIso(ship.x + ship.w * 0.5, ship.y + ship.h * 0.5, camera);
    ctx.save();
    ctx.fillStyle = '#38bdf8';
    ctx.font = 'bold 11px monospace';
    ctx.textAlign = 'center';
    ctx.fillText('DROPSHIP VALKYRIE-04', labelIso.x, labelIso.y - 6);
    ctx.fillStyle = ship.ramp_open ? '#4ade80' : '#facc15';
    ctx.fillText(ship.ramp_open ? '[RAMP DEPLOYED]' : '[PRESSURIZING RAMP...]', labelIso.x, labelIso.y + 10);
    ctx.restore();
  }

  drawExtractionZone(ctx, ext, camera) {
    const pulse = 0.5 + 0.5 * Math.sin(performance.now() * 0.005);
    const fill = ext.active
      ? `rgba(16, 185, 129, ${0.18 + pulse * 0.18})`
      : 'rgba(100, 116, 139, 0.12)';
    const stroke = ext.active ? '#10b981' : '#64748b';

    this.drawIsoQuad(ctx, ext.x, ext.y, ext.w, ext.h, camera, fill, stroke);

    const center = worldToIso(ext.x + ext.w * 0.5, ext.y + ext.h * 0.5, camera);
    ctx.save();
    ctx.textAlign = 'center';
    ctx.font = 'bold 12px monospace';
    ctx.fillStyle = ext.active ? '#34d399' : '#94a3b8';
    ctx.fillText(ext.active ? 'EXTRACTION BEACON ACTIVE' : 'EXTRACTION PAD (LOCKED)', center.x, center.y);
    if (ext.active) {
      ctx.font = '11px monospace';
      ctx.fillStyle = '#a7f3d0';
      ctx.fillText('ALL OPERATIVES GATHER HERE', center.x, center.y + 15);
    }
    ctx.restore();
  }

  drawPuzzles(ctx, puzzles, localPlayer, camera) {
    for (const pz of puzzles) {
      const iso = worldToIso(pz.x, pz.y, camera);
      const isSolved = pz.solved;
      const color = isSolved ? '#10b981' : (pz.color || '#f59e0b');

      // Interaction ring
      ctx.save();
      ctx.beginPath();
      ctx.ellipse(iso.x, iso.y, 26, 13, 0, 0, Math.PI * 2);
      ctx.strokeStyle = color;
      ctx.lineWidth = 1.5;
      ctx.stroke();

      // Terminal pillar
      ctx.fillStyle = isSolved ? '#065f46' : '#78350f';
      ctx.fillRect(iso.x - 8, iso.y - 24, 16, 24);
      ctx.strokeStyle = color;
      ctx.strokeRect(iso.x - 8, iso.y - 24, 16, 24);

      ctx.font = 'bold 11px monospace';
      ctx.textAlign = 'center';
      ctx.fillStyle = color;
      ctx.fillText(isSolved ? 'ONLINE' : 'TERMINAL', iso.x, iso.y - 30);

      if (!isSolved && localPlayer && !localPlayer.downed) {
        const dist = Math.hypot(localPlayer.x - pz.x, localPlayer.y - pz.y);
        if (dist <= pz.radius) {
          ctx.fillStyle = '#38bdf8';
          ctx.font = 'bold 12px monospace';
          ctx.fillText('[PRESS E TO HACK]', iso.x, iso.y - 44);
        }
      }
      ctx.restore();
    }
  }

  drawWires(ctx, wires, camera) {
    for (const wire of wires) {
      if (!wire.path || wire.path.length < 2) continue;
      const wireCol = wire.color || '#f59e0b';
      ctx.save();

      // Convert path coordinates into isometric screen points
      const pts = wire.path.map((p) => worldToIso(p[0], p[1], camera));

      // 1. Dark backing conduit
      ctx.beginPath();
      for (let i = 0; i < pts.length; i++) {
        if (i === 0) ctx.moveTo(pts[i].x, pts[i].y);
        else ctx.lineTo(pts[i].x, pts[i].y);
      }
      ctx.strokeStyle = '#050811';
      ctx.lineWidth = 5;
      ctx.lineCap = 'round';
      ctx.lineJoin = 'round';
      ctx.stroke();

      // 2. Continuous primary color base wire
      ctx.beginPath();
      for (let i = 0; i < pts.length; i++) {
        if (i === 0) ctx.moveTo(pts[i].x, pts[i].y);
        else ctx.lineTo(pts[i].x, pts[i].y);
      }
      ctx.strokeStyle = wireCol;
      ctx.lineWidth = 3.5;
      ctx.lineCap = 'round';
      ctx.lineJoin = 'round';
      ctx.stroke();

      // 3. Repeating black hazard / warning stripes running ACROSS the wire
      // Using setLineDash on top of the colored wire creates alternating colored / black bands across the cable
      ctx.beginPath();
      for (let i = 0; i < pts.length; i++) {
        if (i === 0) ctx.moveTo(pts[i].x, pts[i].y);
        else ctx.lineTo(pts[i].x, pts[i].y);
      }
      ctx.strokeStyle = '#000000';
      ctx.lineWidth = 3.5;
      ctx.setLineDash([5, 5]); // 5px black stripe, 5px colored band across the wire
      ctx.lineCap = 'butt';
      ctx.stroke();

      // 4. Subtle side edges for cable depth
      ctx.setLineDash([]);
      ctx.beginPath();
      for (let i = 0; i < pts.length; i++) {
        if (i === 0) ctx.moveTo(pts[i].x, pts[i].y);
        else ctx.lineTo(pts[i].x, pts[i].y);
      }
      ctx.strokeStyle = 'rgba(0, 0, 0, 0.4)';
      ctx.lineWidth = 5;
      ctx.stroke();

      ctx.restore();
    }
  }

  drawSounds(ctx, sounds, camera) {
    for (const s of sounds) {
      const iso = worldToIso(s.x, s.y, camera);
      const alpha = Math.max(0, Math.min(1, s.ttl / 0.45)) * 0.45;
      const rx = s.radius * TILE_W * 0.35 * (1.1 - s.ttl);
      const ry = rx * 0.5;
      ctx.save();
      ctx.beginPath();
      ctx.ellipse(iso.x, iso.y, Math.max(4, rx), Math.max(2, ry), 0, 0, Math.PI * 2);
      ctx.strokeStyle =
        s.kind === 'alarm'
          ? `rgba(239, 68, 68, ${alpha})`
          : s.kind === 'gunfire'
          ? `rgba(251, 191, 36, ${alpha})`
          : `rgba(56, 189, 248, ${alpha * 0.7})`;
      ctx.lineWidth = 1.5;
      ctx.stroke();
      ctx.restore();
    }
  }

  drawGroundDeployables(ctx, deployables, camera) {
    for (const d of deployables) {
      const iso = worldToIso(d.x, d.y, camera);
      ctx.save();
      if (d.type === 'nano_field') {
        const rx = d.radius * (TILE_W * 0.5);
        const ry = d.radius * (TILE_H * 0.5);
        ctx.beginPath();
        ctx.ellipse(iso.x, iso.y, rx, ry, 0, 0, Math.PI * 2);
        ctx.fillStyle = 'rgba(34, 197, 94, 0.16)';
        ctx.fill();
        ctx.strokeStyle = '#22c55e';
        ctx.lineWidth = 2;
        ctx.stroke();
      } else if (d.type === 'sonar_pulse') {
        const rx = d.radius * (TILE_W * 0.45);
        const ry = rx * 0.5;
        ctx.beginPath();
        ctx.ellipse(iso.x, iso.y, rx, ry, 0, 0, Math.PI * 2);
        ctx.strokeStyle = 'rgba(168, 85, 247, 0.55)';
        ctx.lineWidth = 2;
        ctx.setLineDash([6, 4]);
        ctx.stroke();
      }
      ctx.restore();
    }
  }

  drawTurret(ctx, turret, camera) {
    const iso = worldToIso(turret.x, turret.y, camera);
    ctx.save();
    ctx.fillStyle = '#0284c7';
    ctx.beginPath();
    ctx.arc(iso.x, iso.y - 10, 8, 0, Math.PI * 2);
    ctx.fill();
    ctx.strokeStyle = '#38bdf8';
    ctx.lineWidth = 2;
    ctx.stroke();

    const gunTip = worldToIso(
      turret.x + Math.cos(turret.angle) * 0.7,
      turret.y + Math.sin(turret.angle) * 0.7,
      camera
    );
    ctx.beginPath();
    ctx.moveTo(iso.x, iso.y - 10);
    ctx.lineTo(gunTip.x, gunTip.y - 10);
    ctx.strokeStyle = '#e0f2fe';
    ctx.lineWidth = 3;
    ctx.stroke();
    ctx.restore();
  }

  drawIsoBox(ctx, wall, camera) {
    const hPx =
      wall.type === 'cover'
        ? WALL_HEIGHT_PX * 0.45
        : wall.type === 'ship_ramp'
        ? WALL_HEIGHT_PX * 0.35
        : WALL_HEIGHT_PX;

    const p0 = worldToIso(wall.x, wall.y, camera);
    const p1 = worldToIso(wall.x + wall.w, wall.y, camera);
    const p2 = worldToIso(wall.x + wall.w, wall.y + wall.h, camera);
    const p3 = worldToIso(wall.x, wall.y + wall.h, camera);

    let topColor = '#334155';
    let leftColor = '#1e293b';
    let rightColor = '#0f172a';
    let strokeColor = '#475569';

    if (wall.type === 'door') {
      const baseCol = wall.color || '#f59e0b';
      topColor = baseCol;
      leftColor = baseCol;
      rightColor = '#0f172a';
      strokeColor = baseCol;
    } else if (wall.type === 'ship_hull') {
      topColor = '#1d4ed8';
      leftColor = '#1e40af';
      rightColor = '#1e3a8a';
      strokeColor = '#60a5fa';
    } else if (wall.type === 'cover') {
      topColor = '#3f3f46';
      leftColor = '#27272a';
      rightColor = '#18181b';
      strokeColor = '#52525b';
    }

    ctx.save();
    // South-West face (p3 -> p2)
    ctx.beginPath();
    ctx.moveTo(p3.x, p3.y);
    ctx.lineTo(p2.x, p2.y);
    ctx.lineTo(p2.x, p2.y - hPx);
    ctx.lineTo(p3.x, p3.y - hPx);
    ctx.closePath();
    ctx.fillStyle = leftColor;
    ctx.fill();
    ctx.strokeStyle = strokeColor;
    ctx.lineWidth = 1;
    ctx.stroke();

    // South-East face (p1 -> p2)
    ctx.beginPath();
    ctx.moveTo(p1.x, p1.y);
    ctx.lineTo(p2.x, p2.y);
    ctx.lineTo(p2.x, p2.y - hPx);
    ctx.lineTo(p1.x, p1.y - hPx);
    ctx.closePath();
    ctx.fillStyle = rightColor;
    ctx.fill();
    ctx.stroke();

    // Top roof face
    ctx.beginPath();
    ctx.moveTo(p0.x, p0.y - hPx);
    ctx.lineTo(p1.x, p1.y - hPx);
    ctx.lineTo(p2.x, p2.y - hPx);
    ctx.lineTo(p3.x, p3.y - hPx);
    ctx.closePath();
    ctx.fillStyle = topColor;
    ctx.fill();
    ctx.stroke();
    ctx.restore();
  }

  drawEnemy(ctx, enemy, camera) {
    const iso = worldToIso(enemy.x, enemy.y, camera);
    ctx.save();

    // Draw enemy sight cone on ground
    const fov = enemy.sight_angle || 1.8;
    const range = Math.min(enemy.sight_range || 10.0, 7.5);
    ctx.beginPath();
    ctx.moveTo(iso.x, iso.y);
    const steps = 14;
    for (let i = 0; i <= steps; i++) {
      const a = enemy.angle - fov * 0.5 + (fov * i) / steps;
      const pt = worldToIso(enemy.x + Math.cos(a) * range, enemy.y + Math.sin(a) * range, camera);
      ctx.lineTo(pt.x, pt.y);
    }
    ctx.closePath();
    ctx.fillStyle =
      enemy.state === 'engaged'
        ? 'rgba(239, 68, 68, 0.16)'
        : enemy.state === 'alerted'
        ? 'rgba(245, 158, 11, 0.14)'
        : 'rgba(148, 163, 184, 0.08)';
    ctx.fill();

    // Shadow
    ctx.beginPath();
    ctx.ellipse(iso.x, iso.y, 13, 7, 0, 0, Math.PI * 2);
    ctx.fillStyle = 'rgba(0,0,0,0.5)';
    ctx.fill();

    // Body
    const rPx = enemy.type === 'brute' ? 15 : 11;
    ctx.beginPath();
    ctx.arc(iso.x, iso.y - 14, rPx, 0, Math.PI * 2);
    ctx.fillStyle = enemy.color || '#ef4444';
    ctx.fill();
    ctx.strokeStyle = enemy.state === 'engaged' ? '#ef4444' : '#f8fafc';
    ctx.lineWidth = 2;
    ctx.stroke();

    // HP Bar
    const hpRatio = Math.max(0, enemy.hp / enemy.max_hp);
    ctx.fillStyle = 'rgba(15, 23, 42, 0.85)';
    ctx.fillRect(iso.x - 16, iso.y - 36, 32, 5);
    ctx.fillStyle = '#ef4444';
    ctx.fillRect(iso.x - 16, iso.y - 36, 32 * hpRatio, 5);

    // State badge (! or ?)
    if (enemy.state !== 'patrol') {
      ctx.font = 'bold 13px monospace';
      ctx.textAlign = 'center';
      ctx.fillStyle = enemy.state === 'engaged' ? '#ef4444' : '#facc15';
      ctx.fillText(enemy.state === 'engaged' ? '!' : '?', iso.x, iso.y - 42);
    }

    ctx.restore();
  }

  drawPlayer(ctx, player, isLocal, mouseWorld, camera) {
    const iso = worldToIso(player.x, player.y, camera);
    const cls = this.classesConfig[player.class_id] || {};
    const color = cls.color || '#38bdf8';
    const dt = this.currentDt || 0.016;

    ctx.save();

    // 1. Tactical Ground Ring / Shadow
    ctx.beginPath();
    ctx.ellipse(iso.x, iso.y, 14, 7, 0, 0, Math.PI * 2);
    ctx.fillStyle = 'rgba(0, 0, 0, 0.52)';
    ctx.fill();
    ctx.strokeStyle = isLocal ? '#38bdf8' : (player.downed ? '#ef4444' : 'rgba(255, 255, 255, 0.45)');
    ctx.lineWidth = isLocal ? 2 : 1;
    ctx.stroke();

    // 2. Active Vanguard Shockwave Shield
    if (player.skill_active === 'shockwave_shield') {
      ctx.beginPath();
      ctx.ellipse(iso.x, iso.y - 14, 26, 16, 0, 0, Math.PI * 2);
      ctx.strokeStyle = '#f87171';
      ctx.lineWidth = 3;
      ctx.stroke();
    }

    // 3. Animation State Tracking per Player
    let anim = this.playerAnim.get(player.id);
    if (!anim) {
      anim = {
        phase: 0,
        stride: 0,
        lastX: player.x,
        lastY: player.y,
      };
      this.playerAnim.set(player.id, anim);
    }

    const pvx = player.vx || 0;
    const pvy = player.vy || 0;
    const velSpeed = Math.hypot(pvx, pvy);
    const dispDist = Math.hypot(player.x - anim.lastX, player.y - anim.lastY);
    const dispSpeed = dispDist / Math.max(0.001, dt);
    anim.lastX = player.x;
    anim.lastY = player.y;

    const moveSpeed = Math.max(velSpeed, dispSpeed);
    const isMoving = moveSpeed > 0.08 && !player.downed;

    // Smoothly ease stride amplitude between idle (0.0) and running (1.0)
    const targetStride = isMoving ? Math.min(1.0, moveSpeed / 2.2) : 0.0;
    anim.stride += (targetStride - anim.stride) * Math.min(1.0, dt * 10);

    if (anim.stride > 0.001) {
      const isRunning = Boolean(player.running);
      const walkCadence = isRunning ? 16.5 : (10.5 * Math.max(0.7, Math.min(1.5, moveSpeed / 2.5)));
      anim.phase = (anim.phase + dt * walkCadence) % (Math.PI * 2);
    }

    // 3D Isometric Point Projector (world x, y + screen elevation z in px)
    const project3D = (wx, wy, zPx) => {
      const p = worldToIso(wx, wy, camera);
      return { x: p.x, y: p.y - zPx, depth: wx + wy };
    };

    if (player.downed) {
      this.drawDownedStickFigure(ctx, player, color, project3D);
    } else {
      this.drawHumanoidStickFigure(ctx, player, isLocal, color, anim, mouseWorld, camera, project3D);
    }

    // Callsign & HP Bar above head
    const hpRatio = Math.max(0, player.hp / player.max_hp);
    ctx.fillStyle = 'rgba(15, 23, 42, 0.85)';
    ctx.fillRect(iso.x - 20, iso.y - 48, 40, 5);
    ctx.fillStyle = player.downed ? '#ef4444' : '#22c55e';
    ctx.fillRect(iso.x - 20, iso.y - 48, 40 * hpRatio, 5);

    ctx.font = 'bold 11px Inter, sans-serif';
    ctx.textAlign = 'center';
    ctx.fillStyle = '#f8fafc';
    ctx.fillText(player.downed ? `${player.name} [DOWNED]` : player.name, iso.x, iso.y - 53);

    ctx.restore();
  }

  drawHumanoidStickFigure(ctx, player, isLocal, color, anim, mouseWorld, camera, project3D) {
    const aimAngle = player.angle || 0;
    const aimCos = Math.cos(aimAngle);
    const aimSin = Math.sin(aimAngle);
    // Lateral vector perpendicular to facing angle
    const latCos = -aimSin;
    const latSin = aimCos;

    // Movement angle for legs
    let moveAngle = aimAngle;
    const pvx = player.vx || 0;
    const pvy = player.vy || 0;
    if (Math.hypot(pvx, pvy) > 0.08) {
      moveAngle = Math.atan2(pvy, pvx);
    }
    const walkCos = Math.cos(moveAngle);
    const walkSin = Math.sin(moveAngle);

    // Spine & Pelvis with walk bob & sprint lean
    const isRunning = Boolean(player.running);
    const walkBob = Math.sin(anim.phase * 2) * (isRunning ? 2.0 : 1.2) * anim.stride;
    const zPelvis = 13 + walkBob;
    const pPelvis = project3D(player.x, player.y, zPelvis);

    const leanFactor = isRunning ? 0.08 : 0.04;
    const leanX = walkCos * leanFactor * anim.stride;
    const leanY = walkSin * leanFactor * anim.stride;
    const zShoulders = zPelvis + 12;
    const pShoulders = project3D(player.x + leanX, player.y + leanY, zShoulders);

    // Head
    const zHead = zShoulders + 7;
    const pHead = project3D(player.x + leanX + aimCos * 0.04, player.y + leanY + aimSin * 0.04, zHead);

    // Legs calculation
    const hipDist = 0.12;
    const strideDist = (isRunning ? 0.35 : 0.25) * anim.stride;
    const leftSwing = Math.sin(anim.phase);
    const rightSwing = -leftSwing;

    // Left leg:
    const lHipWorldX = player.x - latCos * hipDist;
    const lHipWorldY = player.y - latSin * hipDist;
    const pLHip = project3D(lHipWorldX, lHipWorldY, zPelvis);

    const lFootSwingWorldX = lHipWorldX + walkCos * (leftSwing * strideDist);
    const lFootSwingWorldY = lHipWorldY + walkSin * (leftSwing * strideDist);
    const zLFoot = Math.max(0, leftSwing) * 4.5 * anim.stride;
    const pLFoot = project3D(lFootSwingWorldX, lFootSwingWorldY, zLFoot);

    const lKneeWorldX = (lHipWorldX + lFootSwingWorldX) * 0.5 + walkCos * 0.04 + aimCos * 0.03;
    const lKneeWorldY = (lHipWorldY + lFootSwingWorldY) * 0.5 + walkSin * 0.04 + aimSin * 0.03;
    const zLKnee = (zPelvis + zLFoot) * 0.5 - 1.0;
    const pLKnee = project3D(lKneeWorldX, lKneeWorldY, zLKnee);

    // Right leg:
    const rHipWorldX = player.x + latCos * hipDist;
    const rHipWorldY = player.y + latSin * hipDist;
    const pRHip = project3D(rHipWorldX, rHipWorldY, zPelvis);

    const rFootSwingWorldX = rHipWorldX + walkCos * (rightSwing * strideDist);
    const rFootSwingWorldY = rHipWorldY + walkSin * (rightSwing * strideDist);
    const zRFoot = Math.max(0, rightSwing) * 4.5 * anim.stride;
    const pRFoot = project3D(rFootSwingWorldX, rFootSwingWorldY, zRFoot);

    const rKneeWorldX = (rHipWorldX + rFootSwingWorldX) * 0.5 + walkCos * 0.04 + aimCos * 0.03;
    const rKneeWorldY = (rHipWorldY + rFootSwingWorldY) * 0.5 + walkSin * 0.04 + aimSin * 0.03;
    const zRKnee = (zPelvis + zRFoot) * 0.5 - 1.0;
    const pRKnee = project3D(rKneeWorldX, rKneeWorldY, zRKnee);

    // Shoulders, Weapon & Hands
    const shoulderDist = 0.16;
    const pLShoulder = project3D(player.x + leanX - latCos * shoulderDist, player.y + leanY - latSin * shoulderDist, zShoulders);
    const pRShoulder = project3D(player.x + leanX + latCos * shoulderDist, player.y + leanY + latSin * shoulderDist, zShoulders);

    const zGun = zPelvis + 5;
    const pStock = project3D(player.x + aimCos * 0.10 + latCos * 0.05, player.y + aimSin * 0.10 + latSin * 0.05, zGun + 1);
    const pGrip = project3D(player.x + aimCos * 0.28 + latCos * 0.05, player.y + aimSin * 0.28 + latSin * 0.05, zGun);
    const pForegrip = project3D(player.x + aimCos * 0.46 - latCos * 0.02, player.y + aimSin * 0.46 - latSin * 0.02, zGun);
    const pMuzzle = project3D(player.x + aimCos * 0.72, player.y + aimSin * 0.72, zGun);

    const pRElbow = {
      x: (pRShoulder.x + pGrip.x) * 0.5 + (latCos - latSin) * 2.5,
      y: (pRShoulder.y + pGrip.y) * 0.5 + 2,
    };
    const pLElbow = {
      x: (pLShoulder.x + pForegrip.x) * 0.5 - (latCos - latSin) * 2.5,
      y: (pLShoulder.y + pForegrip.y) * 0.5 + 2,
    };

    // Aim laser line from gun muzzle for local player
    if (isLocal && mouseWorld) {
      const mIso = worldToIso(mouseWorld.x, mouseWorld.y, camera);
      ctx.beginPath();
      ctx.moveTo(pMuzzle.x, pMuzzle.y);
      ctx.lineTo(mIso.x, mIso.y);
      ctx.strokeStyle = 'rgba(56, 189, 248, 0.35)';
      ctx.lineWidth = 1.5;
      ctx.stroke();
    }

    // Depth sorting for legs (draw far leg before near leg)
    const leftDepth = lHipWorldX + lHipWorldY;
    const rightDepth = rHipWorldX + rHipWorldY;

    const drawLeg = (pHip, pKnee, pFoot) => {
      // Dark outline for contrast
      ctx.beginPath();
      ctx.moveTo(pHip.x, pHip.y);
      ctx.lineTo(pKnee.x, pKnee.y);
      ctx.lineTo(pFoot.x, pFoot.y);
      ctx.strokeStyle = '#020617';
      ctx.lineWidth = 4.5;
      ctx.lineCap = 'round';
      ctx.lineJoin = 'round';
      ctx.stroke();

      // Foreground bone
      ctx.beginPath();
      ctx.moveTo(pHip.x, pHip.y);
      ctx.lineTo(pKnee.x, pKnee.y);
      ctx.lineTo(pFoot.x, pFoot.y);
      ctx.strokeStyle = '#e2e8f0';
      ctx.lineWidth = 2.5;
      ctx.stroke();

      // Foot contact point
      ctx.beginPath();
      ctx.arc(pFoot.x, pFoot.y, 2, 0, Math.PI * 2);
      ctx.fillStyle = '#0f172a';
      ctx.fill();
    };

    // 1. Far leg
    if (leftDepth <= rightDepth) {
      drawLeg(pLHip, pLKnee, pLFoot);
    } else {
      drawLeg(pRHip, pRKnee, pRFoot);
    }

    // 2. Pelvis & Spine
    // Pelvis crossbar
    ctx.beginPath();
    ctx.moveTo(pLHip.x, pLHip.y);
    ctx.lineTo(pRHip.x, pRHip.y);
    ctx.strokeStyle = '#020617';
    ctx.lineWidth = 4.5;
    ctx.lineCap = 'round';
    ctx.stroke();

    ctx.beginPath();
    ctx.moveTo(pLHip.x, pLHip.y);
    ctx.lineTo(pRHip.x, pRHip.y);
    ctx.strokeStyle = '#94a3b8';
    ctx.lineWidth = 2.5;
    ctx.stroke();

    // Spine
    ctx.beginPath();
    ctx.moveTo(pPelvis.x, pPelvis.y);
    ctx.lineTo(pShoulders.x, pShoulders.y);
    ctx.strokeStyle = '#020617';
    ctx.lineWidth = 5.0;
    ctx.lineCap = 'round';
    ctx.stroke();

    ctx.beginPath();
    ctx.moveTo(pPelvis.x, pPelvis.y);
    ctx.lineTo(pShoulders.x, pShoulders.y);
    ctx.strokeStyle = color;
    ctx.lineWidth = 3.0;
    ctx.stroke();

    // Shoulder crossbar
    ctx.beginPath();
    ctx.moveTo(pLShoulder.x, pLShoulder.y);
    ctx.lineTo(pRShoulder.x, pRShoulder.y);
    ctx.strokeStyle = '#020617';
    ctx.lineWidth = 4.5;
    ctx.lineCap = 'round';
    ctx.stroke();

    ctx.beginPath();
    ctx.moveTo(pLShoulder.x, pLShoulder.y);
    ctx.lineTo(pRShoulder.x, pRShoulder.y);
    ctx.strokeStyle = '#cbd5e1';
    ctx.lineWidth = 2.5;
    ctx.stroke();

    // 3. Near leg
    if (leftDepth > rightDepth) {
      drawLeg(pLHip, pLKnee, pLFoot);
    } else {
      drawLeg(pRHip, pRKnee, pRFoot);
    }

    // 4. Arms & Gun
    const drawArm = (pShoulder, pElbow, pHand) => {
      ctx.beginPath();
      ctx.moveTo(pShoulder.x, pShoulder.y);
      ctx.lineTo(pElbow.x, pElbow.y);
      ctx.lineTo(pHand.x, pHand.y);
      ctx.strokeStyle = '#020617';
      ctx.lineWidth = 4.0;
      ctx.lineCap = 'round';
      ctx.lineJoin = 'round';
      ctx.stroke();

      ctx.beginPath();
      ctx.moveTo(pShoulder.x, pShoulder.y);
      ctx.lineTo(pElbow.x, pElbow.y);
      ctx.lineTo(pHand.x, pHand.y);
      ctx.strokeStyle = '#e2e8f0';
      ctx.lineWidth = 2.0;
      ctx.stroke();
    };

    // Right arm (trigger hand)
    drawArm(pRShoulder, pRElbow, pGrip);

    // Rifle
    ctx.beginPath();
    ctx.moveTo(pStock.x, pStock.y);
    ctx.lineTo(pMuzzle.x, pMuzzle.y);
    ctx.strokeStyle = '#020617';
    ctx.lineWidth = 5.0;
    ctx.lineCap = 'round';
    ctx.stroke();

    ctx.beginPath();
    ctx.moveTo(pStock.x, pStock.y);
    ctx.lineTo(pMuzzle.x, pMuzzle.y);
    ctx.strokeStyle = '#475569';
    ctx.lineWidth = 3.0;
    ctx.stroke();

    ctx.beginPath();
    ctx.moveTo(pGrip.x, pGrip.y);
    ctx.lineTo(pMuzzle.x, pMuzzle.y);
    ctx.strokeStyle = '#e2e8f0';
    ctx.lineWidth = 1.5;
    ctx.stroke();

    // Left arm (foregrip support)
    drawArm(pLShoulder, pLElbow, pForegrip);

    // 5. Head & 3D Visor
    // Neck
    ctx.beginPath();
    ctx.moveTo(pShoulders.x, pShoulders.y);
    ctx.lineTo(pHead.x, pHead.y);
    ctx.strokeStyle = '#020617';
    ctx.lineWidth = 4.0;
    ctx.stroke();

    // Spherical Helmet / Head
    ctx.beginPath();
    ctx.arc(pHead.x, pHead.y, 5.5, 0, Math.PI * 2);
    ctx.fillStyle = '#0f172a';
    ctx.fill();
    ctx.strokeStyle = '#f8fafc';
    ctx.lineWidth = 1.8;
    ctx.stroke();

    // 3D Directional Visor pointing along aimAngle
    const visorOffsetScreenX = (aimCos - aimSin) * 2.8;
    const visorOffsetScreenY = (aimCos + aimSin) * 1.4;
    ctx.beginPath();
    ctx.arc(pHead.x + visorOffsetScreenX, pHead.y + visorOffsetScreenY, 2.5, 0, Math.PI * 2);
    ctx.fillStyle = color;
    ctx.fill();
  }

  drawDownedStickFigure(ctx, player, color, project3D) {
    const aimAngle = player.angle || 0;
    const aimCos = Math.cos(aimAngle);
    const aimSin = Math.sin(aimAngle);
    const latCos = -aimSin;
    const latSin = aimCos;

    // Collapsed on ground
    const pPelvis = project3D(player.x, player.y, 2);
    const pShoulders = project3D(player.x - aimCos * 0.28, player.y - aimSin * 0.28, 3);
    const pHead = project3D(player.x - aimCos * 0.44, player.y - aimSin * 0.44, 3);

    const pLHip = project3D(player.x - latCos * 0.12, player.y - latSin * 0.12, 2);
    const pRHip = project3D(player.x + latCos * 0.12, player.y + latSin * 0.12, 2);

    const pLFoot = project3D(player.x + aimCos * 0.25 - latCos * 0.20, player.y + aimSin * 0.25 - latSin * 0.20, 1);
    const pRFoot = project3D(player.x + aimCos * 0.28 + latCos * 0.20, player.y + aimSin * 0.28 + latSin * 0.20, 1);

    const drawDownedBone = (p1, p2) => {
      ctx.beginPath();
      ctx.moveTo(p1.x, p1.y);
      ctx.lineTo(p2.x, p2.y);
      ctx.strokeStyle = '#020617';
      ctx.lineWidth = 4.0;
      ctx.lineCap = 'round';
      ctx.stroke();

      ctx.beginPath();
      ctx.moveTo(p1.x, p1.y);
      ctx.lineTo(p2.x, p2.y);
      ctx.strokeStyle = '#64748b';
      ctx.lineWidth = 2.0;
      ctx.stroke();
    };

    // Legs
    drawDownedBone(pLHip, pLFoot);
    drawDownedBone(pRHip, pRFoot);
    drawDownedBone(pLHip, pRHip);

    // Spine
    drawDownedBone(pPelvis, pShoulders);
    drawDownedBone(pShoulders, pHead);

    // Head
    ctx.beginPath();
    ctx.arc(pHead.x, pHead.y, 5, 0, Math.PI * 2);
    ctx.fillStyle = '#334155';
    ctx.fill();
    ctx.strokeStyle = '#ef4444';
    ctx.lineWidth = 1.5;
    ctx.stroke();
  }

  drawProjectiles(ctx, projectiles, camera) {
    for (const proj of projectiles) {
      const iso = worldToIso(proj.x, proj.y, camera);
      const tail = worldToIso(proj.x - proj.vx * 0.03, proj.y - proj.vy * 0.03, camera);
      ctx.save();
      ctx.beginPath();
      ctx.moveTo(tail.x, tail.y - 14);
      ctx.lineTo(iso.x, iso.y - 14);
      ctx.strokeStyle = proj.color || '#facc15';
      ctx.lineWidth = 3;
      ctx.stroke();

      ctx.beginPath();
      ctx.arc(iso.x, iso.y - 14, 3, 0, Math.PI * 2);
      ctx.fillStyle = '#ffffff';
      ctx.fill();
      ctx.restore();
    }
  }

  drawFogOfWar(ctx, map, visions, camera) {
    const W = this.canvas.width;
    const H = this.canvas.height;
    if (this.fogCanvas.width !== W || this.fogCanvas.height !== H) {
      this.fogCanvas.width = W;
      this.fogCanvas.height = H;
    }

    const fctx = this.fogCtx;
    fctx.clearRect(0, 0, W, H);

    // Fill with dark shroud over the entire viewport
    fctx.fillStyle = 'rgba(4, 7, 13, 0.62)';
    fctx.fillRect(0, 0, W, H);

    // Cut out active line-of-sight polygons for all players
    fctx.globalCompositeOperation = 'destination-out';
    for (const v of visions) {
      if (!v.poly || v.poly.length < 3) continue;
      fctx.beginPath();
      const first = worldToIso(v.poly[0].x, v.poly[0].y, camera);
      fctx.moveTo(first.x, first.y - 12);
      for (let i = 1; i < v.poly.length; i++) {
        const pt = worldToIso(v.poly[i].x, v.poly[i].y, camera);
        fctx.lineTo(pt.x, pt.y - 12);
      }
      fctx.closePath();
      fctx.fillStyle = 'rgba(0, 0, 0, 1)';
      fctx.fill();
    }
    fctx.globalCompositeOperation = 'source-over';

    ctx.drawImage(this.fogCanvas, 0, 0);
  }
}
