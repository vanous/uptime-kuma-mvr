from textual.widget import Widget
from textual.events import MouseDown, MouseMove, MouseUp
from textual.message import Message
from rich.text import Text


class Divider(Widget):
    """A draggable divider widget."""

    class Dragged(Message):
        """Posted when the divider is dragged."""

        control: "Divider"
        __slots__ = ("control", "x")

        def __init__(self, control: "Divider", x: int) -> None:
            self.control = control
            self.x = x
            super().__init__()

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._dragging = False

    def on_mouse_down(self, event: MouseDown) -> None:
        """Called when the mouse is pressed down on the widget."""
        self._dragging = True
        self.capture_mouse()

    def on_mouse_move(self, event: MouseMove) -> None:
        """Called when the mouse moves over the widget."""
        if self._dragging:
            self.post_message(self.Dragged(self, event.screen_x))

    def on_mouse_up(self, event: MouseUp) -> None:
        """Called when the mouse is released."""
        self._dragging = False
        self.release_mouse()
