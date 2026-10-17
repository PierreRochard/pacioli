with Ofx.Chars; use Ofx.Chars;

package body Ofx.Text with SPARK_Mode is

   --  Windows-1252 bytes 16#80# .. 16#9F#; 0 marks the five undefined bytes.
   type Windows_Table is array (16#80# .. 16#9F#) of Code_Point;
   Windows_1252_High : constant Windows_Table :=
     [16#20AC#, 0, 16#201A#, 16#0192#, 16#201E#, 16#2026#, 16#2020#, 16#2021#,
      16#02C6#, 16#2030#, 16#0160#, 16#2039#, 16#0152#, 0, 16#017D#, 0,
      0, 16#2018#, 16#2019#, 16#201C#, 16#201D#, 16#2022#, 16#2013#, 16#2014#,
      16#02DC#, 16#2122#, 16#0161#, 16#203A#, 16#0153#, 0, 16#017E#, 16#0178#];

   Max_Reference : constant := 10;   --  "&#x10FFFF;" less the '&'
   Too_Large     : constant := Code_Point'Last + 1;

   --  A character reference at Text (Position) = '&'. Matched is False when
   --  the ampersand does not start one, and the caller keeps it literally.
   procedure Reference
     (Text     : String;
      Position : Positive;
      Last     : Natural;
      Char     : out Code_Point;
      Next     : out Positive;
      Valid    : out Boolean;
      Matched  : out Boolean)
   with
     Pre  => Text'First = 1 and then Text'Last <= Max_Input
             and then Position <= Last and then Last <= Text'Last
             and then Text (Position) = '&',
     Post => Next in Position + 1 .. Last + 1
             and then (if Matched and then Valid then Is_Printable (Char))
   is
      Semicolon : Natural := 0;
      Name      : Span;
      Value     : Natural := 0;
      Count    : Natural := 0;
      Hex       : Boolean;
      First     : Positive;
   begin
      Char := 0;
      Next := Position + 1;
      Valid := False;
      Matched := False;

      for Index in Position + 1 .. Last loop
         pragma Loop_Invariant (Semicolon = 0);
         exit when Index - Position > Max_Reference;
         if Text (Index) = ';' then
            Semicolon := Index;
            exit;
         end if;
      end loop;
      if Semicolon = 0 then
         return;
      end if;
      pragma Assert (Semicolon in Position + 1 .. Last);

      Name := (First => Position + 1, Last => Semicolon - 1);
      if Matches (Text, Name, "amp") then
         Char := 38;
      elsif Matches (Text, Name, "lt") then
         Char := 60;
      elsif Matches (Text, Name, "gt") then
         Char := 62;
      elsif Matches (Text, Name, "quot") then
         Char := 34;
      elsif Matches (Text, Name, "apos") then
         Char := 39;
      elsif Length (Name) >= 2 and then Text (Name.First) = '#' then
         Hex := Text (Name.First + 1) in 'x' | 'X';
         First := (if Hex then Name.First + 2 else Name.First + 1);
         if First > Name.Last then
            return;
         end if;
         for Index in First .. Name.Last loop
            pragma Loop_Invariant (Count = Index - First);
            pragma Loop_Invariant (Value <= Too_Large);
            if Hex then
               if not Is_Hex (Text (Index)) or else Count = 6 then
                  return;
               end if;
               Value := Value * 16 + Hex_Digit (Text (Index));
            else
               if not Is_Digit (Text (Index)) or else Count = 7 then
                  return;
               end if;
               Value := Value * 10 + Digit (Text (Index));
            end if;
            --  Saturate: the value only grows, so anything past U+10FFFF stays
            --  invalid.
            if Value > Code_Point'Last then
               Value := Too_Large;
            end if;
            Count := Count + 1;
         end loop;
         Matched := True;
         Next := Semicolon + 1;
         if Value <= Code_Point'Last then
            Char := Value;
            Valid := Is_Printable (Char);
         end if;
         return;
      else
         return;
      end if;

      Matched := True;
      Valid := True;
      Next := Semicolon + 1;
   end Reference;

   procedure Decode_Utf_8
     (Text     : String;
      Position : Positive;
      Last     : Natural;
      Char     : out Code_Point;
      Next     : out Positive;
      Valid    : out Boolean)
   with
     Pre  => Text'First = 1 and then Text'Last <= Max_Input
             and then Position <= Last and then Last <= Text'Last
             and then Character'Pos (Text (Position)) >= 16#80#,
     Post => Next in Position + 1 .. Last + 1
             and then (if Valid then Is_Printable (Char))
   is
      Lead : constant Natural := Character'Pos (Text (Position));
      Low  : Natural := 16#80#;   --  the range of the first continuation byte
      High : Natural := 16#BF#;
      B1, B2, B3 : Natural;
      Value : Natural;
   begin
      Char := 0;
      Next := Position + 1;
      Valid := False;

      --  Well-formed sequences only (Unicode table 3-7): no overlong forms,
      --  surrogates, or values past U+10FFFF.
      case Lead is
         when 16#E0# => Low := 16#A0#;
         when 16#ED# => High := 16#9F#;
         when 16#F0# => Low := 16#90#;
         when 16#F4# => High := 16#8F#;
         when others => null;
      end case;

      if Lead in 16#C2# .. 16#DF# then
         if Last - Position < 1 then
            return;
         end if;
         B1 := Character'Pos (Text (Position + 1));
         if B1 not in 16#80# .. 16#BF# then
            return;
         end if;
         Value := (Lead - 16#C0#) * 64 + (B1 - 16#80#);
         Next := Position + 2;
      elsif Lead in 16#E0# .. 16#EF# then
         if Last - Position < 2 then
            return;
         end if;
         B1 := Character'Pos (Text (Position + 1));
         B2 := Character'Pos (Text (Position + 2));
         if B1 not in Low .. High or else B2 not in 16#80# .. 16#BF# then
            return;
         end if;
         Value := ((Lead - 16#E0#) * 64 + (B1 - 16#80#)) * 64 + (B2 - 16#80#);
         Next := Position + 3;
      elsif Lead in 16#F0# .. 16#F4# then
         if Last - Position < 3 then
            return;
         end if;
         B1 := Character'Pos (Text (Position + 1));
         B2 := Character'Pos (Text (Position + 2));
         B3 := Character'Pos (Text (Position + 3));
         if B1 not in Low .. High or else B2 not in 16#80# .. 16#BF#
           or else B3 not in 16#80# .. 16#BF#
         then
            return;
         end if;
         Value := (((Lead - 16#F0#) * 64 + (B1 - 16#80#)) * 64 + (B2 - 16#80#)) * 64
                  + (B3 - 16#80#);
         Next := Position + 4;
      else
         return;
      end if;

      if Value <= Code_Point'Last then
         Char := Value;
         Valid := Is_Printable (Char);
      end if;
   end Decode_Utf_8;

   procedure Decode
     (Text       : String;
      Position   : Positive;
      Last       : Natural;
      Encoding   : Charset;
      References : Boolean;
      Char       : out Code_Point;
      Next       : out Positive;
      Valid      : out Boolean)
   is
      Byte    : constant Natural := Character'Pos (Text (Position));
      Matched : Boolean;
   begin
      Char := 0;
      Next := Position + 1;
      Valid := False;

      if References and then Text (Position) = '&' then
         Reference (Text, Position, Last, Char, Next, Valid, Matched);
         if not Matched then
            Char := 38;
            Next := Position + 1;
            Valid := True;
         end if;
         return;
      end if;

      if Byte < 16#80# then
         Char := Byte;
         Valid := Is_Printable (Char);
         return;
      end if;

      case Encoding is
         when Us_Ascii =>
            null;
         when Latin_1 =>
            Char := Byte;
            Valid := Is_Printable (Char);
         when Windows_1252 =>
            if Byte <= 16#9F# then
               Char := Windows_1252_High (Byte);
            else
               Char := Byte;
            end if;
            Valid := Char /= 0 and then Is_Printable (Char);
         when Utf_8 =>
            Decode_Utf_8 (Text, Position, Last, Char, Next, Valid);
      end case;
   end Decode;

   procedure Measure
     (Text       : String;
      S          : Span;
      Encoding   : Charset;
      References : Boolean;
      Count      : out Natural;
      Valid      : out Boolean)
   is
      Position : Positive := S.First;
      Next     : Positive;
      Char     : Code_Point;
   begin
      Count := 0;
      Valid := True;
      if Is_Empty (S) then
         return;
      end if;
      while Position <= S.Last loop
         pragma Loop_Invariant (Position in S.First .. S.Last);
         pragma Loop_Invariant (Count <= Position - S.First);
         pragma Loop_Variant (Increases => Position);
         pragma Warnings (GNATprove, Off, "is set by", Reason => "Measuring needs only validity");
         Decode (Text, Position, S.Last, Encoding, References, Char, Next, Valid);
         pragma Warnings (GNATprove, On, "is set by");
         if not Valid then
            return;
         end if;
         Count := Count + 1;
         Position := Next;
      end loop;
   end Measure;

end Ofx.Text;
