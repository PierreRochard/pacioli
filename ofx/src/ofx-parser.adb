with Ofx.Header;
with Ofx.Lexer;  use Ofx.Lexer;
with Ofx.Tags;   use Ofx.Tags;
with Ofx.Values; use Ofx.Values;

package body Ofx.Parser with SPARK_Mode is

   --  The aggregate a frame stands for. Skipped is an ignored or private
   --  aggregate, read only for well-formedness.
   type Context is
     (Root, Ofx_Body, Signon_Messages, Sonrs, Fi, Status, Bank_Messages, Card_Messages,
      Trnrs, Stmtrs, Bank_Account, Card_Account, Tranlist, Stmttrn, Currency_Aggregate,
      Ledgerbal, Availbal, Skipped);

   type Status_Owner is (Signon_Owner, Statement_Owner);

   type Frame is record
      Ctx   : Context := Root;
      Name  : Span := No_Span;
      Owner : Status_Owner := Signon_Owner;
   end record;

   type Frame_Array is array (1 .. Max_Depth) of Frame;

   type State is record
      Frames   : Frame_Array;
      Depth    : Natural range 0 .. Max_Depth := 0;
      Position : Positive := 1;
      Closed   : Boolean := False;   --  </OFX> has been read
      Failed   : Boolean := False;
   end record;

   function Frames_Within (Text : String; S : State) return Boolean is
     (for all I in 1 .. S.Depth => Within (S.Frames (I).Name, Text));

   function Current (S : State) return Context is
     (if S.Depth = 0 then Root else S.Frames (S.Depth).Ctx);

   --  Where an element's value goes.
   type Target_Kind is (To_Status, To_Signon, To_Statement, To_Transaction);

   type Target is record
      Kind          : Target_Kind := To_Signon;
      Status_F      : Status_Field := Code;
      Signon_F      : Signon_Field := Dtserver;
      Statement_F   : Statement_Field := Trnuid;
      Transaction_F : Transaction_Field := Trntype;
   end record;

   type Action_Kind is
     (Open,               --  a recognized aggregate
      Leaf,               --  a recognized element, stored
      Ignore_Leaf,        --  an informational element
      Ignore_Aggregate,   --  an informational aggregate
      Undetermined,       --  private or inside an ignored aggregate
      Unsupported);

   type Action is record
      Kind : Action_Kind := Unsupported;
      Ctx  : Context := Skipped;
      Goal : Target;
   end record;

   Unsupported_Action      : constant Action := (Kind => Unsupported, others => <>);
   Ignore_Leaf_Action      : constant Action := (Kind => Ignore_Leaf, others => <>);
   Ignore_Aggregate_Action : constant Action := (Kind => Ignore_Aggregate, others => <>);
   Undetermined_Action     : constant Action := (Kind => Undetermined, others => <>);

   function Opens (C : Context) return Action is ((Kind => Open, Ctx => C, Goal => <>));

   function To (F : Status_Field) return Action is
     ((Kind => Leaf, Ctx => Skipped, Goal => (Kind => To_Status, Status_F => F, others => <>)));

   function To (F : Signon_Field) return Action is
     ((Kind => Leaf, Ctx => Skipped, Goal => (Kind => To_Signon, Signon_F => F, others => <>)));

   function To (F : Statement_Field) return Action is
     ((Kind => Leaf, Ctx => Skipped,
       Goal => (Kind => To_Statement, Statement_F => F, others => <>)));

   function To (F : Transaction_Field) return Action is
     ((Kind => Leaf, Ctx => Skipped,
       Goal => (Kind => To_Transaction, Transaction_F => F, others => <>)));

   --  The grammar: what a tag means inside each recognized aggregate.
   function Classify (Parent : Context; T : Tag; Kind : Statement_Kind) return Action is
     (case Parent is
         when Root =>
           (if T = T_OFX then Opens (Ofx_Body) else Unsupported_Action),
         when Ofx_Body =>
           (case T is
               when T_SIGNONMSGSRSV1     => Opens (Signon_Messages),
               when T_BANKMSGSRSV1       => Opens (Bank_Messages),
               when T_CREDITCARDMSGSRSV1 => Opens (Card_Messages),
               when others               => Unsupported_Action),
         when Signon_Messages =>
           (if T = T_SONRS then Opens (Sonrs) else Unsupported_Action),
         when Sonrs =>
           (case T is
               when T_STATUS   => Opens (Status),
               when T_DTSERVER => To (Dtserver),
               when T_LANGUAGE => To (Language),
               when T_FI       => Opens (Fi),
               when T_USERKEY | T_TSKEYEXPIRE | T_DTPROFUP | T_DTACCTUP | T_SESSCOOKIE
                  | T_ACCESSKEY => Ignore_Leaf_Action,
               when others     => Unsupported_Action),
         when Fi =>
           (case T is
               when T_ORG  => To (Org),
               when T_FID  => To (Fid),
               when others => Unsupported_Action),
         when Status =>
           (case T is
               when T_CODE     => To (Code),
               when T_SEVERITY => To (Severity),
               when T_MESSAGE  => To (Message),
               when others     => Unsupported_Action),
         when Bank_Messages =>
           (if T = T_STMTTRNRS then Opens (Trnrs) else Unsupported_Action),
         when Card_Messages =>
           (if T = T_CCSTMTTRNRS then Opens (Trnrs) else Unsupported_Action),
         when Trnrs =>
           (case T is
               when T_TRNUID    => To (Trnuid),
               when T_STATUS    => Opens (Status),
               when T_CLTCOOKIE => Ignore_Leaf_Action,
               when T_STMTRS    => (if Kind = Bank then Opens (Stmtrs) else Unsupported_Action),
               when T_CCSTMTRS  => (if Kind = Card then Opens (Stmtrs) else Unsupported_Action),
               when others      => Unsupported_Action),
         when Stmtrs =>
           (case T is
               when T_CURDEF       => To (Curdef),
               when T_BANKACCTFROM =>
                 (if Kind = Bank then Opens (Bank_Account) else Unsupported_Action),
               when T_CCACCTFROM   =>
                 (if Kind = Card then Opens (Card_Account) else Unsupported_Action),
               when T_BANKTRANLIST => Opens (Tranlist),
               when T_LEDGERBAL    => Opens (Ledgerbal),
               when T_AVAILBAL     => Opens (Availbal),
               --  Marketing text, other balances, and pending transactions.
               when T_MKTGINFO     => Ignore_Leaf_Action,
               when T_BALLIST | T_BANKTRANLISTP => Ignore_Aggregate_Action,
               --  Card rewards, cash-advance limits, and interest rates.
               when T_REWARDINFO   =>
                 (if Kind = Card then Ignore_Aggregate_Action else Unsupported_Action),
               when T_CASHADVBALAMT | T_INTRATEPURCH | T_INTRATECASH | T_INTRATEXFER =>
                 (if Kind = Card then Ignore_Leaf_Action else Unsupported_Action),
               when others         => Unsupported_Action),
         when Bank_Account =>
           (case T is
               when T_BANKID   => To (Bankid),
               when T_BRANCHID => To (Branchid),
               when T_ACCTID   => To (Acctid),
               when T_ACCTTYPE => To (Accttype),
               when T_ACCTKEY  => To (Acctkey),
               when others     => Unsupported_Action),
         when Card_Account =>
           (case T is
               when T_ACCTID  => To (Acctid),
               when T_ACCTKEY => To (Acctkey),
               when others    => Unsupported_Action),
         when Tranlist =>
           (case T is
               when T_DTSTART => To (Dtstart),
               when T_DTEND   => To (Dtend),
               when T_STMTTRN => Opens (Stmttrn),
               when others    => Unsupported_Action),
         when Stmttrn =>
           (case T is
               when T_TRNTYPE       => To (Trntype),
               when T_DTPOSTED      => To (Dtposted),
               when T_DTUSER        => To (Dtuser),
               when T_DTAVAIL       => To (Dtavail),
               when T_TRNAMT        => To (Trnamt),
               when T_FITID         => To (Fitid),
               when T_CORRECTFITID  => To (Correctfitid),
               when T_CORRECTACTION => To (Correctaction),
               when T_SRVRTID       => To (Srvrtid),
               when T_CHECKNUM      => To (Checknum),
               when T_REFNUM        => To (Refnum),
               when T_SIC           => To (Sic),
               when T_PAYEEID       => To (Payeeid),
               when T_NAME          => To (Name),
               when T_MEMO          => To (Memo),
               when T_INV401KSOURCE => To (Inv401ksource),
               when T_CURRENCY | T_ORIGCURRENCY => Opens (Currency_Aggregate),
               when others          => Unsupported_Action),
         when Currency_Aggregate =>
           (case T is
               when T_CURRATE => To (Currate),
               when T_CURSYM  => To (Cursym),
               when others    => Unsupported_Action),
         when Ledgerbal =>
           (case T is
               when T_BALAMT => To (Ledger_Balamt),
               when T_DTASOF => To (Ledger_Dtasof),
               when others   => Unsupported_Action),
         when Availbal =>
           (case T is
               when T_BALAMT => To (Avail_Balamt),
               when T_DTASOF => To (Avail_Dtasof),
               when others   => Unsupported_Action),
         when Skipped =>
           Undetermined_Action);

   --  The error at Position: the open aggregates, then Leaf if any.
   function Failure
     (Text     : String;
      S        : State;
      Kind     : Error_Kind;
      Leaf     : Span;
      Position : Natural) return Error_Record
   with
     Pre  => Frames_Within (Text, S) and then Within (Leaf, Text),
     Post => Well_Formed (Text, Failure'Result)
   is
      Error : Error_Record :=
        (Kind => Kind, Position => Position, Depth => 0, Path => [others => No_Span]);
   begin
      for I in 1 .. S.Depth loop
         Error.Path (I) := S.Frames (I).Name;
         Error.Depth := I;
         pragma Loop_Invariant (Error.Depth = I);
         pragma Loop_Invariant (for all J in 1 .. I => Within (Error.Path (J), Text));
      end loop;
      if not Is_Empty (Leaf) then
         Error.Depth := S.Depth + 1;
         Error.Path (S.Depth + 1) := Leaf;
      end if;
      return Error;
   end Failure;

   procedure Fail
     (Text     : String;
      S        : in out State;
      Error    : out Error_Record;
      Kind     : Error_Kind;
      Leaf     : Span;
      Position : Natural)
   with
     Pre  => Frames_Within (Text, S) and then Within (Leaf, Text),
     Post => Well_Formed (Text, Error) and then S.Failed
             and then Frames_Within (Text, S) and then S.Depth = S.Depth'Old
             and then S.Position = S.Position'Old
   is
   begin
      Error := Failure (Text, S, Kind, Leaf, Position);
      S.Failed := True;
   end Fail;

   --  An element's value: the character data after its start tag, then its
   --  optional end tag. Position starts just after the start tag.
   procedure Read_Value
     (Text     : String;
      Tag_Kind : Token_Kind;
      Name     : Span;
      Position : in out Positive;
      Value    : out Span)
   with
     Pre  => Text'First = 1 and then Text'Last <= Max_Input
             and then Position <= Text'Last + 1 and then Within (Name, Text),
     Post => Position in Position'Old .. Text'Last + 1 and then Within (Value, Text)
   is
      Data  : Token;
      After : Token;
   begin
      Value := No_Span;
      if Tag_Kind = Empty_Tag then
         return;
      end if;
      Next (Text, Position, Data);
      if Data.Kind = Text_Run then
         Value := Data.Value;
         Position := Data.Next;
         Next (Text, Position, After);
         if After.Kind = End_Tag and then Same (Text, After.Name, Name) then
            Position := After.Next;
         end if;
      elsif Data.Kind = End_Tag and then Same (Text, Data.Name, Name) then
         Position := Data.Next;
      end if;
   end Read_Value;

   --  Record one occurrence of an element in F.
   procedure Check
     (Text     : String;
      F        : in out Field;
      R        : Rule;
      Encoding : Charset;
      Value    : Span;
      Problem  : out Error_Kind)
   with
     Pre  => Text'First = 1 and then Text'Last <= Max_Input and then Within (Value, Text),
     Post => Problem in No_Error | Element_Duplicate | Value_Invalid
   is
   begin
      Problem := No_Error;
      if F.Seen then
         Problem := Element_Duplicate;
      elsif not Is_Empty (Value) and then not Valid (Text, Value, R, Encoding) then
         Problem := Value_Invalid;
      else
         F := (Seen => True, Value => Value);
      end if;
   end Check;

   procedure Store
     (Text     : String;
      Doc      : in out Document;
      S        : in out State;
      Error    : in out Error_Record;
      Goal     : Target;
      Name     : Span;
      Value    : Span)
   with
     Pre  => Text'First = 1 and then Text'Last <= Max_Input
             and then Frames_Within (Text, S) and then not S.Failed
             and then Within (Name, Text) and then Within (Value, Text),
     Post => Frames_Within (Text, S) and then S.Depth = S.Depth'Old
             and then S.Position = S.Position'Old
             and then (if S.Failed then Well_Formed (Text, Error))
   is
      Encoding  : constant Charset := Doc.Header.Decoding;
      Statement : constant Natural := Doc.Statement_Count;
      Item      : constant Natural := Doc.Transaction_Count;
      Problem   : Error_Kind := Syntax_Invalid;
   begin
      case Goal.Kind is
         when To_Status =>
            if S.Depth = 0 then
               null;
            elsif S.Frames (S.Depth).Owner = Signon_Owner then
               Check (Text, Doc.Signon.Status.Fields (Goal.Status_F), Status_Rule (Goal.Status_F),
                      Encoding, Value, Problem);
            elsif Statement >= 1 then
               Check (Text, Doc.Statements (Statement).Status.Fields (Goal.Status_F),
                      Status_Rule (Goal.Status_F), Encoding, Value, Problem);
            end if;
         when To_Signon =>
            Check (Text, Doc.Signon.Fields (Goal.Signon_F), Signon_Rule (Goal.Signon_F),
                   Encoding, Value, Problem);
         when To_Statement =>
            if Statement >= 1 then
               Check (Text, Doc.Statements (Statement).Fields (Goal.Statement_F),
                      Statement_Rule (Goal.Statement_F), Encoding, Value, Problem);
            end if;
         when To_Transaction =>
            if Item >= 1 then
               Check (Text, Doc.Transactions (Item).Fields (Goal.Transaction_F),
                      Transaction_Rule (Goal.Transaction_F), Encoding, Value, Problem);
            end if;
      end case;
      if Problem /= No_Error then
         Fail (Text, S, Error, Problem, Name, S.Position);
      end if;
   end Store;

   --  Enter an aggregate, recording that it occurred.
   procedure Open
     (Text  : String;
      Doc   : in out Document;
      S     : in out State;
      Error : in out Error_Record;
      Ctx   : Context;
      T     : Tag;
      Name  : Span)
   with
     Pre  => Text'First = 1 and then Text'Last <= Max_Input
             and then Frames_Within (Text, S) and then not S.Failed
             and then Within (Name, Text),
     Post => Frames_Within (Text, S) and then S.Position = S.Position'Old
             and then (if S.Failed then Well_Formed (Text, Error)
                       else S.Depth = S.Depth'Old + 1)
   is
      Statement : constant Natural := Doc.Statement_Count;
      Item      : constant Natural := Doc.Transaction_Count;
      Owner     : Status_Owner := Signon_Owner;
      Duplicate : Boolean := False;
      Orphan    : Boolean := False;   --  no statement or transaction to fill
   begin
      if S.Depth = Max_Depth then
         Fail (Text, S, Error, Limit_Exceeded, Name, S.Position);
         return;
      end if;

      case Ctx is
         when Signon_Messages =>
            Duplicate := Doc.Signon_Messages;
            Doc.Signon_Messages := True;
         when Sonrs =>
            Duplicate := Doc.Signon.Seen;
            Doc.Signon.Seen := True;
         when Fi =>
            Duplicate := Doc.Signon.Has_Fi;
            Doc.Signon.Has_Fi := True;
         when Status =>
            if Current (S) = Sonrs then
               Duplicate := Doc.Signon.Status.Seen;
               Doc.Signon.Status.Seen := True;
            elsif Statement >= 1 then
               Owner := Statement_Owner;
               Duplicate := Doc.Statements (Statement).Status.Seen;
               Doc.Statements (Statement).Status.Seen := True;
            else
               Orphan := True;
            end if;
         when Bank_Messages =>
            Duplicate := Doc.Bank_Messages;
            Doc.Bank_Messages := True;
         when Card_Messages =>
            Duplicate := Doc.Card_Messages;
            Doc.Card_Messages := True;
         when Trnrs =>
            if Statement = Max_Statements then
               Fail (Text, S, Error, Limit_Exceeded, Name, S.Position);
               return;
            end if;
            Doc.Statements (Statement + 1) :=
              (Kind              => (if Current (S) = Card_Messages then Card else Bank),
               First_Transaction => Item + 1,
               Last_Transaction  => Item,
               others            => <>);
            Doc.Statement_Count := Statement + 1;
         when Stmtrs | Bank_Account | Card_Account | Tranlist | Ledgerbal | Availbal =>
            if Statement = 0 then
               Orphan := True;
            else
               declare
                  R : Statement_Record renames Doc.Statements (Statement);
               begin
                  case Ctx is
                     when Stmtrs =>
                        Duplicate := R.Has_Response;
                        R.Has_Response := True;
                     when Bank_Account | Card_Account =>
                        Duplicate := R.Has_Account;
                        R.Has_Account := True;
                     when Tranlist =>
                        Duplicate := R.Has_Tranlist;
                        R.Has_Tranlist := True;
                     when Ledgerbal =>
                        Duplicate := R.Has_Ledgerbal;
                        R.Has_Ledgerbal := True;
                     when others =>
                        Duplicate := R.Has_Availbal;
                        R.Has_Availbal := True;
                  end case;
               end;
            end if;
         when Stmttrn =>
            if Item = Max_Transactions then
               Fail (Text, S, Error, Limit_Exceeded, Name, S.Position);
               return;
            elsif Statement = 0 then
               Orphan := True;
            else
               Doc.Transactions (Item + 1) := (others => <>);
               Doc.Transaction_Count := Item + 1;
               Doc.Statements (Statement).Last_Transaction := Item + 1;
            end if;
         when Currency_Aggregate =>
            if Item = 0 then
               Orphan := True;
            else
               Duplicate := Doc.Transactions (Item).Currency /= No_Currency;
               Doc.Transactions (Item).Currency :=
                 (if T = T_ORIGCURRENCY then Original_Currency else Currency);
            end if;
         when Root | Ofx_Body | Skipped =>
            null;
      end case;

      if Orphan then
         Fail (Text, S, Error, Syntax_Invalid, Name, S.Position);
      elsif Duplicate then
         Fail (Text, S, Error, Element_Duplicate, Name, S.Position);
      else
         S.Depth := S.Depth + 1;
         S.Frames (S.Depth) := (Ctx => Ctx, Name => Name, Owner => Owner);
      end if;
   end Open;

   procedure Close (S : in out State)
   with
     Pre  => S.Depth >= 1,
     Post => S.Depth = S.Depth'Old - 1 and then S.Frames = S.Frames'Old
             and then S.Position = S.Position'Old and then S.Failed = S.Failed'Old
   is
   begin
      S.Depth := S.Depth - 1;
      if S.Depth = 0 then
         S.Closed := True;
      end if;
   end Close;

   --  A start tag; on return S.Position is past everything consumed.
   procedure Start
     (Text  : String;
      Doc   : in out Document;
      S     : in out State;
      Error : in out Error_Record;
      Tok   : Token)
   with
     Pre  => Text'First = 1 and then Text'Last <= Max_Input
             and then Frames_Within (Text, S) and then not S.Failed
             and then Tok.Kind in Start_Tag | Empty_Tag
             and then Tok.Next > S.Position and then Tok.Next <= Text'Last + 1
             and then Within (Tok.Name, Text),
     Post => Frames_Within (Text, S)
             and then (if S.Failed then Well_Formed (Text, Error)
                       else S.Position in Tok.Next .. Text'Last + 1)
   is
      T        : constant Tag := Lookup (Text, Tok.Name);
      Parent   : constant Context := Current (S);
      Kind     : constant Statement_Kind :=
        (if Doc.Statement_Count = 0 then Bank
         else Doc.Statements (Doc.Statement_Count).Kind);
      A        : Action;
      Value    : Span;
      Position : Positive := Tok.Next;
      Peek     : Token;
   begin
      if Parent /= Root and then Is_Private (Text, Tok.Name) then
         A := Undetermined_Action;
      else
         A := Classify (Parent, T, Kind);
      end if;

      --  An undetermined tag followed by data is an element; otherwise it
      --  starts an aggregate that is read and ignored.
      if A.Kind = Undetermined then
         if Tok.Kind = Empty_Tag then
            A := Ignore_Leaf_Action;
         else
            Next (Text, Tok.Next, Peek);
            A := (if Peek.Kind = Text_Run and then not Is_Empty (Peek.Value)
                  then Ignore_Leaf_Action else Ignore_Aggregate_Action);
         end if;
      end if;

      case A.Kind is
         when Unsupported | Undetermined =>
            Fail (Text, S, Error, Element_Unsupported, Tok.Name, S.Position);
         when Leaf =>
            Read_Value (Text, Tok.Kind, Tok.Name, Position, Value);
            Store (Text, Doc, S, Error, A.Goal, Tok.Name, Value);
            if not S.Failed then
               S.Position := Position;
            end if;
         when Ignore_Leaf =>
            pragma Warnings (GNATprove, Off, "is set by", Reason => "An ignored element's value is skipped");
            Read_Value (Text, Tok.Kind, Tok.Name, Position, Value);
            pragma Warnings (GNATprove, On, "is set by");
            S.Position := Position;
         when Open | Ignore_Aggregate =>
            Open (Text, Doc, S, Error, (if A.Kind = Open then A.Ctx else Skipped), T, Tok.Name);
            if not S.Failed then
               S.Position := Tok.Next;
               if Tok.Kind = Empty_Tag then
                  Close (S);
               end if;
            end if;
      end case;
   end Start;

   procedure Parse (Text : String; Doc : in out Document; Error : out Error_Record) is
      S         : State;
      Tok       : Token;
      Header_Ok : Boolean;
   begin
      Error := (others => <>);
      Doc.Signon_Messages := False;
      Doc.Bank_Messages := False;
      Doc.Card_Messages := False;
      Doc.Signon := (others => <>);
      Doc.Statement_Count := 0;
      Doc.Transaction_Count := 0;

      Ofx.Header.Parse (Text, Doc.Header, Header_Ok);
      if not Header_Ok then
         Error.Kind := Header_Invalid;
         return;
      end if;
      S.Position := Doc.Header.Body_Start;

      loop
         pragma Loop_Invariant (S.Position <= Text'Last + 1);
         pragma Loop_Invariant (Frames_Within (Text, S));
         pragma Loop_Invariant (not S.Failed);
         pragma Loop_Variant (Increases => S.Position);

         Next (Text, S.Position, Tok);
         case Tok.Kind is
            when End_Of_Input =>
               if S.Closed then
                  Error := (others => <>);
               else
                  Error := Failure (Text, S, Syntax_Invalid, No_Span, S.Position);
               end if;
               return;
            when Malformed =>
               Error := Failure (Text, S, Syntax_Invalid, No_Span, S.Position);
               return;
            when Comment =>
               S.Position := Tok.Next;
            when Text_Run =>
               --  Only white space may stand between tags of aggregates.
               if not Is_Empty (Tok.Value) then
                  Error := Failure (Text, S, Syntax_Invalid, No_Span, S.Position);
                  return;
               end if;
               S.Position := Tok.Next;
            when End_Tag =>
               if S.Depth = 0 or else not Same (Text, Tok.Name, S.Frames (S.Depth).Name) then
                  Error := Failure (Text, S, Syntax_Invalid, Tok.Name, S.Position);
                  return;
               end if;
               Close (S);
               S.Position := Tok.Next;
            when Start_Tag | Empty_Tag =>
               if S.Closed then
                  Error := Failure (Text, S, Syntax_Invalid, Tok.Name, S.Position);
                  return;
               end if;
               Start (Text, Doc, S, Error, Tok);
               if S.Failed then
                  return;
               end if;
         end case;
      end loop;
   end Parse;

end Ofx.Parser;
