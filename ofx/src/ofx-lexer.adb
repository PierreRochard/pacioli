with Ofx.Chars; use Ofx.Chars;

package body Ofx.Lexer with SPARK_Mode is

   --  Text (First .. Last) without leading and trailing white space.
   function Trim (Text : String; First : Positive; Last : Natural) return Span
   with
     Pre  => Text'First = 1 and then Text'Last <= Max_Input
             and then First <= Text'Last and then Last <= Text'Last
             and then First <= Last + 1,
     Post => Within (Trim'Result, Text)
   is
      Left  : Positive := First;
      Right : Natural := Last;
   begin
      while Left <= Right and then Is_Space (Text (Left)) loop
         pragma Loop_Invariant (Left in First .. Right and then Right = Last);
         pragma Loop_Variant (Increases => Left);
         Left := Left + 1;
      end loop;
      while Right >= Left and then Is_Space (Text (Right)) loop
         pragma Loop_Invariant (Right in Left .. Last);
         pragma Loop_Variant (Decreases => Right);
         Right := Right - 1;
      end loop;
      if Right < Left then
         return No_Span;
      end if;
      return (First => Left, Last => Right);
   end Trim;

   procedure Comment_Token (Text : String; From : Positive; Result : out Token)
   with
     Pre  => Text'First = 1 and then Text'Last <= Max_Input
             and then From <= Text'Last and then Text (From) = '<',
     Post => Result.Next in From .. Text'Last + 1
             and then Result.Kind in Comment | Malformed
             and then (if Result.Kind = Comment then Result.Next > From)
             and then Result.Name = No_Span and then Result.Value = No_Span
   is
      Position : Positive;
   begin
      Result := (Kind => Malformed, Name => No_Span, Value => No_Span, Next => From);
      if From > Text'Last - 3
        or else Text (From + 1) /= '!' or else Text (From + 2) /= '-'
        or else Text (From + 3) /= '-'
      then
         return;
      end if;
      Position := From + 4;
      while Position <= Text'Last - 2 loop
         pragma Loop_Invariant (Position in From + 4 .. Text'Last - 2);
         pragma Loop_Variant (Increases => Position);
         if Text (Position) = '-' and then Text (Position + 1) = '-'
           and then Text (Position + 2) = '>'
         then
            Result := (Kind => Comment, Name => No_Span, Value => No_Span,
                       Next => Position + 3);
            return;
         end if;
         Position := Position + 1;
      end loop;
   end Comment_Token;

   procedure Next (Text : String; From : Positive; Result : out Token) is
      Position : Positive;
      Start    : Positive;
      Closing  : Boolean;
      Name     : Span;
   begin
      Result := (Kind => End_Of_Input, Name => No_Span, Value => No_Span, Next => From);
      if From > Text'Last then
         return;
      end if;

      if Text (From) /= '<' then
         Position := From;
         while Position < Text'Last and then Text (Position + 1) /= '<' loop
            pragma Loop_Invariant (Position in From .. Text'Last - 1);
            pragma Loop_Variant (Increases => Position);
            Position := Position + 1;
         end loop;
         Result := (Kind  => Text_Run, Name => No_Span,
                    Value => Trim (Text, From, Position), Next => Position + 1);
         return;
      end if;

      if From = Text'Last then
         Result.Kind := Malformed;
         return;
      end if;

      if Text (From + 1) = '!' then
         Comment_Token (Text, From, Result);
         return;
      end if;

      Closing := Text (From + 1) = '/';
      Start := (if Closing then From + 2 else From + 1);
      if Start > Text'Last or else not Is_Upper (Text (Start)) then
         Result.Kind := Malformed;
         return;
      end if;

      Position := Start;
      while Position < Text'Last and then Is_Tag (Text (Position + 1)) loop
         pragma Loop_Invariant (Position in Start .. Text'Last - 1);
         pragma Loop_Invariant (Position - Start < Max_Name);
         pragma Loop_Variant (Increases => Position);
         if Position - Start + 1 = Max_Name then
            Result.Kind := Malformed;
            return;
         end if;
         Position := Position + 1;
      end loop;
      Name := (First => Start, Last => Position);

      --  XML permits white space before the closing '>'.
      Position := Position + 1;
      while Position <= Text'Last and then Is_Space (Text (Position)) loop
         pragma Loop_Invariant (Position in Start + 1 .. Text'Last);
         pragma Loop_Variant (Increases => Position);
         Position := Position + 1;
      end loop;

      if Position <= Text'Last and then Text (Position) = '>' then
         Result := (Kind  => (if Closing then End_Tag else Start_Tag),
                    Name  => Name, Value => No_Span, Next => Position + 1);
      elsif not Closing and then Position < Text'Last
        and then Text (Position) = '/' and then Text (Position + 1) = '>'
      then
         Result := (Kind => Empty_Tag, Name => Name, Value => No_Span,
                    Next => Position + 2);
      else
         Result.Kind := Malformed;
      end if;
   end Next;

end Ofx.Lexer;
