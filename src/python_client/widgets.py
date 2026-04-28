"""Small pygame widgets used by the teleop UI."""
from __future__ import annotations

import pygame


class Slider:
    def __init__(
        self,
        rect: pygame.Rect,
        label: str,
        vmin: float,
        vmax: float,
        value: float,
        step: float = 0.0,
        fmt: str = "{:.2f}",
    ) -> None:
        self.rect = rect
        self.label = label
        self.vmin = vmin
        self.vmax = vmax
        self.value = max(vmin, min(vmax, value))
        self.step = step
        self.fmt = fmt
        self.dragging = False

    def handle_event(self, event: pygame.event.Event) -> bool:
        before = self.value
        if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            if self.rect.collidepoint(event.pos):
                self.dragging = True
                self._set_from_x(event.pos[0])
        elif event.type == pygame.MOUSEBUTTONUP and event.button == 1:
            self.dragging = False
        elif event.type == pygame.MOUSEMOTION and self.dragging:
            self._set_from_x(event.pos[0])
        return self.value != before

    def _set_from_x(self, x: int) -> None:
        frac = (x - self.rect.x) / max(1, self.rect.width)
        frac = max(0.0, min(1.0, frac))
        v = self.vmin + frac * (self.vmax - self.vmin)
        if self.step > 0:
            v = round(v / self.step) * self.step
        self.value = max(self.vmin, min(self.vmax, v))

    def draw(self, screen: pygame.Surface, font: pygame.font.Font) -> None:
        pygame.draw.rect(screen, (60, 60, 70), self.rect, border_radius=3)
        frac = (self.value - self.vmin) / max(1e-9, self.vmax - self.vmin)
        fill = self.rect.copy()
        fill.width = int(self.rect.width * frac)
        pygame.draw.rect(screen, (90, 140, 200), fill, border_radius=3)
        hx = self.rect.x + int(self.rect.width * frac)
        pygame.draw.circle(screen, (220, 220, 220), (hx, self.rect.centery), 6)
        text = f"{self.label} {self.fmt.format(self.value)}"
        screen.blit(font.render(text, True, (220, 220, 220)), (self.rect.x, self.rect.y - 16))


class Button:
    def __init__(self, rect: pygame.Rect, label: str) -> None:
        self.rect = rect
        self.label = label
        self.hover = False
        self._click_pending = False

    def handle_event(self, event: pygame.event.Event) -> bool:
        if event.type == pygame.MOUSEMOTION:
            self.hover = self.rect.collidepoint(event.pos)
        elif event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            if self.rect.collidepoint(event.pos):
                self._click_pending = True
        elif event.type == pygame.MOUSEBUTTONUP and event.button == 1:
            if self._click_pending and self.rect.collidepoint(event.pos):
                self._click_pending = False
                return True
            self._click_pending = False
        return False

    def draw(self, screen: pygame.Surface, font: pygame.font.Font, accent: bool = False) -> None:
        if accent:
            color = (200, 120, 60) if not self.hover else (220, 150, 80)
        else:
            color = (60, 100, 60) if not self.hover else (80, 130, 80)
        pygame.draw.rect(screen, color, self.rect, border_radius=4)
        surf = font.render(self.label, True, (240, 240, 240))
        screen.blit(surf, surf.get_rect(center=self.rect.center))


class ChoiceRow:
    """Horizontal row of mutually exclusive labelled buttons."""

    def __init__(
        self,
        rect: pygame.Rect,
        label: str,
        choices: list[str],
        selected_index: int = 0,
        gap: int = 4,
    ) -> None:
        self.rect = rect
        self.label = label
        self.choices = choices
        self.selected_index = max(0, min(len(choices) - 1, selected_index))
        self.gap = gap
        self._hover_index: int | None = None

    def _cell_rects(self) -> list[pygame.Rect]:
        n = len(self.choices)
        if n == 0:
            return []
        total_gap = self.gap * (n - 1)
        cell_w = max(1, (self.rect.width - total_gap) // n)
        rects: list[pygame.Rect] = []
        x = self.rect.x
        for i in range(n):
            w = cell_w if i < n - 1 else self.rect.right - x
            rects.append(pygame.Rect(x, self.rect.y, w, self.rect.height))
            x += w + self.gap
        return rects

    def handle_event(self, event: pygame.event.Event) -> bool:
        cells = self._cell_rects()
        if event.type == pygame.MOUSEMOTION:
            self._hover_index = None
            for i, r in enumerate(cells):
                if r.collidepoint(event.pos):
                    self._hover_index = i
                    break
            return False
        if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            for i, r in enumerate(cells):
                if r.collidepoint(event.pos) and i != self.selected_index:
                    self.selected_index = i
                    return True
        return False

    def draw(self, screen: pygame.Surface, font: pygame.font.Font) -> None:
        screen.blit(
            font.render(self.label, True, (220, 220, 220)),
            (self.rect.x, self.rect.y - 16),
        )
        for i, r in enumerate(self._cell_rects()):
            if i == self.selected_index:
                bg = (90, 140, 200)
            elif i == self._hover_index:
                bg = (75, 75, 90)
            else:
                bg = (55, 55, 65)
            pygame.draw.rect(screen, bg, r, border_radius=3)
            txt = font.render(self.choices[i], True, (240, 240, 240))
            screen.blit(txt, txt.get_rect(center=r.center))
