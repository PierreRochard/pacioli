--  ASCII character classes used by the OFX grammar.

package Ofx.Chars with SPARK_Mode, Pure is

   function Is_Digit (C : Character) return Boolean is (C in '0' .. '9');

   function Is_Hex (C : Character) return Boolean is
     (C in '0' .. '9' | 'a' .. 'f' | 'A' .. 'F');

   function Is_Upper (C : Character) return Boolean is (C in 'A' .. 'Z');

   function Is_Alphanumeric (C : Character) return Boolean is
     (C in 'A' .. 'Z' | 'a' .. 'z' | '0' .. '9');

   function Is_Space (C : Character) return Boolean is
     (C in ' ' | ASCII.HT | ASCII.LF | ASCII.VT | ASCII.FF | ASCII.CR);

   --  OFX element names: upper-case letters, digits, and the '.' that marks a
   --  private extension such as INTU.BID.
   function Is_Tag (C : Character) return Boolean is
     (Is_Upper (C) or else Is_Digit (C) or else C = '.');

   function Digit (C : Character) return Natural is
     (Character'Pos (C) - Character'Pos ('0'))
   with Pre => Is_Digit (C), Post => Digit'Result <= 9;

   function Hex_Digit (C : Character) return Natural is
     (case C is
         when '0' .. '9' => Character'Pos (C) - Character'Pos ('0'),
         when 'a' .. 'f' => Character'Pos (C) - Character'Pos ('a') + 10,
         when 'A' .. 'F' => Character'Pos (C) - Character'Pos ('A') + 10,
         when others     => 0)
   with Pre => Is_Hex (C), Post => Hex_Digit'Result <= 15;

end Ofx.Chars;
