import chess
import Levenshtein
from typing import List, Tuple

class MoveValidator:
    """
    Validates tokens against the current chess board.
    Incorporates Levenshtein distance to salvage slightly mangled OCR reads.
    """
    
    def __init__(self):
        self.board = chess.Board()

    def set_board_fen(self, fen: str):
        """Reset the internal board state to a given FEN."""
        self.board.set_fen(fen)

    def is_legal(self, san_move: str) -> bool:
        """Strict check if python-chess considers the SAN valid right now."""
        try:
            return self.board.parse_san(san_move) in self.board.legal_moves
        except ValueError:
            return False

    def push_san(self, san_move: str) -> chess.Move:
        """Push a known valid move onto the board."""
        return self.board.push_san(san_move)

    def pop(self):
        """Pop the last move."""
        self.board.pop()

    def get_legal_sans(self) -> List[str]:
        """Returns all legal moves in SAN format for the current position."""
        return [self.board.san(move) for move in self.board.legal_moves]

    def attempt_levenshtein_rescue(self, bad_san: str, threshold: int = 2) -> Tuple[bool, str]:
        """
        Calculates distance from bad_san to all legal SANs.
        If a legal SAN is within distance `threshold` AND is the absolute closest
        (and hopefully unique at that distance), return True and the best SAN.
        """
        legal_sans = self.get_legal_sans()
        if not legal_sans:
            return False, ""

        # Score them
        scored = [(Levenshtein.distance(bad_san, san), san) for san in legal_sans]
        scored.sort(key=lambda x: x[0])
        
        best_dist, best_san = scored[0]
        
        if best_dist <= threshold:
            # Check for ambiguity (two legal moves equidistant)
            if len(scored) > 1 and scored[1][0] == best_dist:
                # E.g. OCR is 'Bh4' and both 'Bh3' and 'Bh5' are legal
                # Too ambiguous to rescue automatically with confidence
                return False, ""
                
            return True, best_san
            
        return False, ""
